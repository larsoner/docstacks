"""Deploy a built HTML tree into a version directory of a site repository.

The repository is an existing local checkout that the caller is responsible for
producing -- typically a shallow, sparse clone made by a CI job. Everything at
its root that ``docstacks`` does not own (``CNAME``, ``.nojekyll``, a landing
``index.html``, foreign version directories, aliases it was not asked about)
survives a deploy untouched; that invariant is the whole point of the module.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from docstacks import _git
from docstacks.manifest import Manifest
from docstacks.tree import PREFERRED_ALIAS, VERSION_RE, _pick_alias, version_key

__all__ = ["DeployError", "deploy"]

DEFAULT_MANIFEST = "versions.json"


class DeployError(RuntimeError):
    """A deploy was refused, or could not be completed."""


def deploy(
    html_dir: str | os.PathLike[str],
    version: str,
    repo_dir: str | os.PathLike[str],
    *,
    aliases: Sequence[str] = (),
    base_url: str | None = None,
    name: str | None = None,
    manifest_path: str | None = DEFAULT_MANIFEST,
    source_sha: str | None = None,
    message: str | None = None,
    push: bool = False,
) -> str:
    """Deploy built documentation into a site repository and commit the result.

    Parameters
    ----------
    html_dir : path-like
        Directory of already-built HTML. It must contain an ``index.html``;
        ``docstacks`` never runs a documentation builder itself.
    version : str
        Version identity, used both as the directory name at the repository root
        and as the manifest ``version`` field.
    repo_dir : path-like
        Checkout of the site repository. Must be a clean git working tree.
    aliases : sequence of str
        Alias names to point at ``version``, created as relative symlinks at the
        repository root. An alias that already exists as a real directory is an
        error rather than something to overwrite. Aliases are not remembered
        between deploys: redeploying a version without repeating its alias moves
        the manifest URL back to the version directory.
    base_url : str | None
        Absolute URL the site is served from. Required when adding a new
        manifest entry; when updating an existing one it is inferred from that
        entry's current URL.
    name : str | None
        Display label for the manifest entry. Defaults to ``"<version>
        (<alias>)"`` when aliased, and is left alone when updating an entry that
        already has one.
    manifest_path : str | None
        Manifest to update, relative to ``repo_dir``. ``None`` skips the
        manifest entirely.
    source_sha : str | None
        Revision of the source repository that produced ``html_dir``, recorded
        as a ``Source-sha:`` commit trailer.
    message : str | None
        Commit subject. Defaults to ``"Deploy <version> docs"``.
    push : bool
        Whether to run ``git push origin HEAD`` afterwards. Configuring the
        remote and its credentials is the caller's job.

    Returns
    -------
    sha : str
        Full SHA of the commit that was created.
    """
    html_dir = Path(html_dir)
    repo_dir = Path(repo_dir)
    aliases = tuple(aliases)

    _check_inputs(html_dir, version, repo_dir, aliases, manifest_path)

    target = repo_dir / version
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif os.path.lexists(target):
        target.unlink()
    shutil.copytree(html_dir, target, symlinks=True)

    for alias in aliases:
        link = repo_dir / alias
        if link.is_symlink():
            link.unlink()
        os.symlink(version, link, target_is_directory=True)

    if manifest_path is not None:
        full = repo_dir / manifest_path
        manifest = Manifest.load(full) if full.is_file() else Manifest()
        _update_manifest(manifest, version, aliases, base_url, name)
        manifest.dump(full)

    staged = [version, *aliases]
    if manifest_path is not None:
        staged.append(manifest_path)
    _git.git(repo_dir, "add", "-A", "--", *staged)
    if not _git.has_staged_changes(repo_dir):
        raise DeployError(
            f"nothing to deploy: {version} in {repo_dir} already matches {html_dir}"
        )

    trailers = f"Deployed-version: {version}"
    if source_sha is not None:
        trailers += f"\nSource-sha: {source_sha}"
    _git.git(
        repo_dir, "commit", "-m", message or f"Deploy {version} docs", "-m", trailers
    )
    sha = _git.git(repo_dir, "rev-parse", "HEAD")

    if push:
        _git.git(repo_dir, "push", "origin", "HEAD")
    return sha


def _check_inputs(
    html_dir: Path,
    version: str,
    repo_dir: Path,
    aliases: Sequence[str],
    manifest_path: str | None,
) -> None:
    """Refuse everything refusable before touching the repository."""
    _check_component(version, "version")
    if not html_dir.is_dir():
        raise DeployError(f"{html_dir} is not a directory")
    if not (html_dir / "index.html").is_file():
        raise DeployError(
            f"{html_dir} contains no index.html; is the documentation build empty?"
        )
    if manifest_path is not None and os.path.isabs(manifest_path):
        raise DeployError(
            f"manifest_path {manifest_path!r} must be relative to repo_dir"
        )

    if not repo_dir.is_dir():
        raise DeployError(f"{repo_dir} is not a directory")
    if not _git.is_worktree(repo_dir):
        raise DeployError(f"{repo_dir} is not a git working tree")
    status = _git.git(repo_dir, "status", "--porcelain")
    if status:
        raise DeployError(
            f"{repo_dir} has uncommitted changes, refusing to deploy:\n{status}"
        )

    for alias in aliases:
        _check_component(alias, "alias")
        if alias == version:
            raise DeployError(f"alias {alias!r} is the same as the version")
        link = repo_dir / alias
        if not link.is_symlink() and link.exists():
            raise DeployError(
                f"{link} already exists and is not a symlink; move or delete it "
                "by hand before deploying an alias over it"
            )


def _check_component(value: str, what: str) -> None:
    if not value or value in (os.curdir, os.pardir) or {"/", "\\"} & set(value):
        raise DeployError(f"invalid {what} {value!r}: must be a single path component")


def _update_manifest(
    manifest: Manifest,
    version: str,
    aliases: Sequence[str],
    base_url: str | None,
    name: str | None,
) -> None:
    """Insert or refresh the entry for ``version``, keeping the file's order."""
    entry = manifest.get(version)
    alias = _pick_alias(list(aliases))
    if base_url is None:
        if entry is None:
            raise DeployError(
                f"base_url is required to add a manifest entry for {version!r}"
            )
        base_url = _base_of(entry.url)
    if not base_url.endswith("/"):
        base_url += "/"
    url = base_url + (alias or version) + "/"
    if name is None and alias is not None:
        name = f"{version} ({alias})"

    if entry is None:
        manifest.add(version, url, name, position=_insert_position(manifest, version))
    else:
        entry.url = url
        if name is not None:
            entry.name = name
    if PREFERRED_ALIAS in aliases:
        manifest.set_preferred(version)


def _base_of(url: str) -> str:
    """Strip the final path segment of a version URL to recover the site root."""
    scheme, separator, rest = url.partition("://")
    path = rest.rstrip("/")
    if not separator or "/" not in path:
        raise DeployError(f"cannot infer base_url from {url!r}, pass it explicitly")
    return f"{scheme}://{path.rsplit('/', 1)[0]}/"


def _insert_position(manifest: Manifest, version: str) -> int:
    """Index at which ``version`` belongs among the numbered entries."""
    entries = manifest.entries
    index = 0
    while index < len(entries) and not VERSION_RE.match(entries[index].version):
        index += 1
    key = version_key(version)
    while index < len(entries):
        other = entries[index].version
        if not VERSION_RE.match(other) or version_key(other) < key:
            break
        index += 1
    return index
