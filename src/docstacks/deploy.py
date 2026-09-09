"""Deploy a built HTML tree into a version directory of a site repository.

The repository is an existing local checkout that the caller is responsible for
producing -- typically a shallow, sparse clone made by a CI job. Everything at
its root that ``docstacks`` does not own (``CNAME``, ``.nojekyll``, a landing
``index.html``, foreign version directories, aliases it was not asked about)
survives a deploy untouched; that invariant is the whole point of the module.

:func:`deploy` writes what it is told, and counts the aliases already pointing
at the version being deployed -- so a bugfix redeploy of the current stable
release stays stable without repeating ``--alias stable``, and is not relabeled
for having inherited one. :func:`promote` is the release-day variant that also
demotes whatever the new alias just took over, so the manifest never claims that
``/stable/`` serves a version it no longer does.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path

from docstacks import _git
from docstacks._repo import (
    DeployError,
    check_component,
    check_repo,
    commit,
    stage,
)
from docstacks._repo import push as push_branch
from docstacks.manifest import Entry, Manifest
from docstacks.tree import (
    PREFERRED_ALIAS,
    VERSION_RE,
    _aliases_pointing_at,
    _pick_alias,
    version_key,
)

__all__ = ["DeployError", "deploy", "promote"]

DEFAULT_MANIFEST = "versions.json"


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
        error rather than something to overwrite. Aliases already pointing at
        ``version`` are kept: redeploying without repeating them leaves their
        symlinks alone and keeps the manifest URL they supply.
    base_url : str | None
        Absolute URL the site is served from. Required when adding a new
        manifest entry; when updating an existing one it is inferred from that
        entry's current URL.
    name : str | None
        Display label for the manifest entry. Defaults to ``"<version>
        (<alias>)"`` when an alias is requested; an alias merely inherited from
        the previous deploy generates no label, so an entry retitled by hand
        keeps its name across a redeploy.
    manifest_path : str | None
        Manifest to update, relative to ``repo_dir``. ``None`` skips the
        manifest entirely.
    source_sha : str | None
        Revision of the source repository that produced ``html_dir``, recorded
        as a ``Source-sha:`` commit trailer.
    message : str | None
        Commit subject. Defaults to ``"Deploy <version> docs"``.
    push : bool
        Whether to push the current branch to ``origin`` afterwards. Configuring
        the remote and its credentials is the caller's job.

    Returns
    -------
    sha : str
        Full SHA of the commit that was created.
    """
    return _deploy(
        html_dir,
        version,
        repo_dir,
        aliases=aliases,
        base_url=base_url,
        name=name,
        manifest_path=manifest_path,
        source_sha=source_sha,
        subject=message or f"Deploy {version} docs",
        push=push,
        demote=False,
    )


def promote(
    html_dir: str | os.PathLike[str],
    version: str,
    repo_dir: str | os.PathLike[str],
    *,
    aliases: Sequence[str] = (PREFERRED_ALIAS,),
    base_url: str | None = None,
    name: str | None = None,
    manifest_path: str | None = DEFAULT_MANIFEST,
    source_sha: str | None = None,
    message: str | None = None,
    push: bool = False,
) -> str:
    """Deploy a version, alias it, and demote its predecessor, in one commit.

    Everything :func:`deploy` does, plus: any *other* entry that was preferred,
    or whose URL pointed at one of the aliases being retargeted, is rewritten to
    point at its own version directory. Its display name is cleared when it was
    the generated ``"<version> (<alias>)"`` label -- which would otherwise go on
    claiming a status it no longer has -- and kept when someone chose it by hand.

    Doing this in the same commit as the content is what makes release day
    atomic: there is no intermediate state in which ``/stable/`` serves the new
    version while the manifest still advertises it as the old one.

    Parameters
    ----------
    html_dir : path-like
        Directory of already-built HTML.
    version : str
        Version being promoted.
    repo_dir : path-like
        Checkout of the site repository. Must be a clean git working tree.
    aliases : sequence of str
        Aliases to point at ``version``, defaulting to ``("stable",)``. Aliases
        already pointing at ``version`` supply its URL and preferred flag too,
        without being recreated.
    base_url : str | None
        Absolute URL the site is served from, also used for the demoted URLs.
    name : str | None
        Display label for the promoted entry.
    manifest_path : str | None
        Manifest to update, relative to ``repo_dir``.
    source_sha : str | None
        Revision that produced ``html_dir``, recorded as a trailer.
    message : str | None
        Commit subject. Defaults to ``"Promote <version> to stable"``.
    push : bool
        Whether to push the current branch to ``origin`` afterwards.

    Returns
    -------
    sha : str
        Full SHA of the commit that was created.
    """
    return _deploy(
        html_dir,
        version,
        repo_dir,
        aliases=aliases,
        base_url=base_url,
        name=name,
        manifest_path=manifest_path,
        source_sha=source_sha,
        subject=message or f"Promote {version} to stable",
        push=push,
        demote=True,
    )


def _deploy(
    html_dir: str | os.PathLike[str],
    version: str,
    repo_dir: str | os.PathLike[str],
    *,
    aliases: Sequence[str],
    base_url: str | None,
    name: str | None,
    manifest_path: str | None,
    source_sha: str | None,
    subject: str,
    push: bool,
    demote: bool,
) -> str:
    html_path = Path(html_dir)
    repo_path = Path(repo_dir)
    alias_names = tuple(aliases)

    _check_inputs(html_path, version, repo_path, alias_names, manifest_path, push)

    # counted but never rewritten, so a chain such as stable -> 2.1 -> 2.1.3 survives
    existing = tuple(
        alias
        for alias in _aliases_pointing_at(repo_path, version)
        if alias not in alias_names
    )

    target = repo_path / version
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif os.path.lexists(target):
        target.unlink()
    shutil.copytree(html_path, target, symlinks=True)

    for alias in alias_names:
        link = repo_path / alias
        if link.is_symlink():
            link.unlink()
        os.symlink(version, link, target_is_directory=True)

    staged = [version, *alias_names]
    if manifest_path is not None:
        full = repo_path / manifest_path
        manifest = Manifest.load(full) if full.is_file() else Manifest()
        _update_manifest(
            manifest, version, alias_names, existing, base_url, name, demote
        )
        manifest.dump(full)
        staged.append(manifest_path)

    stage(repo_path, staged)
    if not _git.has_staged_changes(repo_path):
        raise DeployError(
            f"nothing to deploy: {version} in {repo_path} already matches {html_path}"
        )

    trailers = f"Deployed-version: {version}"
    if source_sha is not None:
        trailers += f"\nSource-sha: {source_sha}"
    sha = commit(repo_path, subject, trailers)
    if push:
        push_branch(repo_path)
    return sha


def _check_inputs(
    html_dir: Path,
    version: str,
    repo_dir: Path,
    aliases: Sequence[str],
    manifest_path: str | None,
    pushing: bool,
) -> None:
    """Refuse everything refusable before touching the repository."""
    check_component(version, "version")
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

    check_repo(repo_dir, push=pushing)

    for alias in aliases:
        check_component(alias, "alias")
        if alias == version:
            raise DeployError(f"alias {alias!r} is the same as the version")
        link = repo_dir / alias
        if not link.is_symlink() and link.exists():
            raise DeployError(
                f"{link} already exists and is not a symlink; move or delete it "
                "by hand before deploying an alias over it"
            )


def _update_manifest(
    manifest: Manifest,
    version: str,
    aliases: Sequence[str],
    inferred: Sequence[str],
    base_url: str | None,
    name: str | None,
    demote: bool,
) -> None:
    """Insert or refresh the entry for ``version``, keeping the file's order."""
    base = _resolve_base_url(manifest, version, base_url)
    holders = (*aliases, *inferred)
    outgoing = _outgoing(manifest, version, holders, base) if demote else []

    entry = manifest.get(version)
    alias = _pick_alias(list(holders))
    url = base + (alias or version) + "/"
    # an inferred alias supplies the URL but never a label, so a retitle survives
    if name is None and aliases:
        name = f"{version} ({alias})"
    if entry is None:
        manifest.add(version, url, name, position=_insert_position(manifest, version))
    else:
        entry.url = url
        if name is not None:
            entry.name = name
    if PREFERRED_ALIAS in holders:
        manifest.set_preferred(version)

    for stale in outgoing:
        if stale.name in {f"{stale.version} ({holder})" for holder in holders}:
            stale.name = None
        stale.url = base + stale.version + "/"


def _resolve_base_url(manifest: Manifest, version: str, base_url: str | None) -> str:
    """The site root, from the caller or from the URL already on record."""
    if base_url is None:
        entry = manifest.get(version)
        if entry is None:
            raise DeployError(
                f"base_url is required to add a manifest entry for {version!r}"
            )
        base_url = _base_of(entry.url)
    return base_url if base_url.endswith("/") else base_url + "/"


def _outgoing(
    manifest: Manifest, version: str, aliases: Sequence[str], base_url: str
) -> list[Entry]:
    """Entries the incoming version is about to take an alias away from."""
    alias_urls = {base_url + alias + "/" for alias in aliases}
    return [
        entry
        for entry in manifest
        if entry.version != version and (entry.preferred or entry.url in alias_urls)
    ]


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
