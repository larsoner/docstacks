"""Lifecycle operations on an already-deployed site: retitle, delete, prune.

None of these build or rebuild anything. ``retitle`` touches only the manifest,
``delete`` removes one version directory and its entry, and ``prune`` rewrites
the branch's history without touching its content.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from docstacks import _git
from docstacks._repo import (
    DeployError,
    check_component,
    check_repo,
    commit,
    require_branch,
    stage,
)
from docstacks._repo import push as push_branch
from docstacks.deploy import DEFAULT_MANIFEST
from docstacks.manifest import Manifest
from docstacks.tree import _aliases_pointing_at

__all__ = ["PruneResult", "delete", "prune", "retitle"]

#: Subject of the synthetic commit that replaces everything before the anchor.
SQUASH_SUBJECT = "Squashed history (docstacks prune)"


@dataclass
class PruneResult:
    """What a :func:`prune` did.

    Parameters
    ----------
    branch : str
        Branch that was rewritten.
    tip : str
        Full SHA of the new tip.
    kept : int
        Commits preserved, counting the anchor.
    squashed : int
        Commits collapsed into the new root. Zero means nothing was rewritten.
    """

    branch: str
    tip: str
    kept: int
    squashed: int


def retitle(
    version: str,
    name: str | None,
    repo_dir: str | os.PathLike[str],
    *,
    manifest_path: str = DEFAULT_MANIFEST,
    message: str | None = None,
    push: bool = False,
) -> str:
    """Change the display label of a manifest entry and commit.

    Parameters
    ----------
    version : str
        Version identity to relabel.
    name : str | None
        New label. ``None`` or an empty string clears it, so the switcher falls
        back to displaying the version itself.
    repo_dir : path-like
        Checkout of the site repository. Must be a clean git working tree.
    manifest_path : str
        Manifest to edit, relative to ``repo_dir``.
    message : str | None
        Commit subject. Defaults to ``"Retitle <version>"``.
    push : bool
        Whether to push the current branch to ``origin`` afterwards.

    Returns
    -------
    sha : str
        Full SHA of the commit that was created.
    """
    repo_path = Path(repo_dir)
    check_component(version, "version")
    check_repo(repo_path, push=push)

    full = repo_path / manifest_path
    if not full.is_file():
        raise DeployError(f"{full} does not exist")
    manifest = Manifest.load(full)
    if manifest.get(version) is None:
        raise DeployError(f"{full} has no entry for {version!r}")
    manifest.retitle(version, name or None)
    manifest.dump(full)

    stage(repo_path, [manifest_path])
    if not _git.has_staged_changes(repo_path):
        raise DeployError(f"nothing to change: {version!r} is already titled that way")
    sha = commit(
        repo_path, message or f"Retitle {version}", f"Retitled-version: {version}"
    )
    if push:
        push_branch(repo_path)
    return sha


def delete(
    version: str,
    repo_dir: str | os.PathLike[str],
    *,
    manifest_path: str = DEFAULT_MANIFEST,
    message: str | None = None,
    push: bool = False,
) -> str:
    """Remove a version directory and its manifest entry, and commit.

    Refused while anything still points at the version: a root symlink aliasing
    it, or a ``preferred`` flag on its entry. Deleting under either would leave
    the site serving a dangling alias or advertising a version that is gone, so
    the caller has to promote a replacement first.

    Either half may be missing: a manifest entry with no directory on disk, or a
    tracked directory the manifest never listed, is removed on its own. Staging
    follows what git tracks rather than what is on disk, so a manifest-only
    entry does not send git a pathspec it cannot match.

    Parameters
    ----------
    version : str
        Version identity to remove.
    repo_dir : path-like
        Checkout of the site repository. Must be a clean git working tree.
    manifest_path : str
        Manifest to edit, relative to ``repo_dir``.
    message : str | None
        Commit subject. Defaults to ``"Delete <version> docs"``.
    push : bool
        Whether to push the current branch to ``origin`` afterwards.

    Returns
    -------
    sha : str
        Full SHA of the commit that was created.
    """
    repo_path = Path(repo_dir)
    check_component(version, "version")
    check_repo(repo_path, push=push)

    target = repo_path / version
    full = repo_path / manifest_path
    manifest = Manifest.load(full) if full.is_file() else Manifest()
    entry = manifest.get(version)
    deployed = os.path.lexists(target)
    tracked = bool(_git.git(repo_path, "ls-files", "--", version))
    if not tracked and entry is None:
        raise DeployError(
            f"nothing to delete: {repo_path} tracks no {version!r} and "
            f"{manifest_path} has no entry for it"
        )
    if entry is not None and entry.preferred:
        raise DeployError(
            f"{version!r} is the preferred version; promote another version first"
        )
    holders = _aliases_pointing_at(repo_path, version)
    if holders:
        raise DeployError(
            f"{version!r} is still aliased by {', '.join(holders)}; "
            "promote another version first"
        )

    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    elif deployed:
        target.unlink()
    staged = [version] if tracked else []
    if entry is not None:
        manifest.remove(version)
        manifest.dump(full)
        staged.append(manifest_path)

    stage(repo_path, staged)
    sha = commit(
        repo_path,
        message or f"Delete {version} docs",
        f"Deleted-version: {version}",
    )
    if push:
        push_branch(repo_path)
    return sha


def prune(
    repo_dir: str | os.PathLike[str],
    *,
    keep: int | None = None,
    keep_since: str | None = None,
    push: bool = False,
) -> PruneResult:
    """Collapse history older than an anchor into a single root commit.

    Every commit from the anchor to ``HEAD`` survives with its tree, message,
    author, and author date intact; everything before it becomes one synthetic
    root. The rewrite is done with ``git commit-tree`` rather than a rebase:
    replaying diffs across multi-gigabyte documentation trees is hopeless, while
    re-parenting existing tree objects is cheap and cannot alter content.

    Parameters
    ----------
    repo_dir : path-like
        Checkout of the site repository. Must be a clean git working tree with a
        branch checked out.
    keep : int | None
        Number of commits to preserve, counting ``HEAD``. Mutually exclusive
        with ``keep_since``.
    keep_since : str | None
        Revision to use as the anchor, preserved along with everything after it.
    push : bool
        Whether to ``git push --force-with-lease`` afterwards. This rewrites
        published history; every existing clone of the branch becomes invalid.

    Returns
    -------
    result : PruneResult
        Branch, new tip, and how many commits were kept and collapsed. A
        ``squashed`` of zero means the anchor was already the root and nothing
        was rewritten.
    """
    repo_path = Path(repo_dir)
    if (keep is None) == (keep_since is None):
        raise DeployError("pass exactly one of keep or keep_since")
    check_repo(repo_path, push=push)
    branch = require_branch(repo_path)

    total = int(_git.git(repo_path, "rev-list", "--count", "HEAD"))
    anchor = keep_since
    if keep is not None:
        if keep < 1:
            raise DeployError(f"keep must be at least 1, got {keep}")
        if keep > total:
            raise DeployError(f"cannot keep {keep} commits, {branch} has only {total}")
        anchor = f"HEAD~{keep - 1}"

    anchor_sha = _git.try_git(
        repo_path, "rev-parse", "--verify", "--quiet", f"{anchor}^{{commit}}"
    )
    if anchor_sha is None:
        raise DeployError(f"unknown revision {anchor!r}")
    if (
        _git.try_git(repo_path, "merge-base", "--is-ancestor", anchor_sha, "HEAD")
        is None
    ):
        raise DeployError(f"{anchor!r} is not an ancestor of HEAD")

    kept = int(_git.git(repo_path, "rev-list", "--count", f"{anchor_sha}..HEAD")) + 1
    parent = _git.try_git(
        repo_path, "rev-parse", "--verify", "--quiet", f"{anchor_sha}^"
    )
    if parent is None:
        head = _git.git(repo_path, "rev-parse", "HEAD")
        return PruneResult(branch=branch, tip=head, kept=kept, squashed=0)

    base_tree = _git.git(repo_path, "rev-parse", f"{parent}^{{tree}}")
    tip = _git.git(repo_path, "commit-tree", base_tree, "-m", SQUASH_SUBJECT)
    replayed = _git.git(repo_path, "rev-list", "--reverse", f"{anchor_sha}^..HEAD")
    for sha in replayed.splitlines():
        tip = _replay(repo_path, sha, tip)

    _git.git(repo_path, "update-ref", f"refs/heads/{branch}", tip)
    _git.git(repo_path, "reset", "--hard")
    if push:
        push_branch(repo_path, force_with_lease=True)
    return PruneResult(branch=branch, tip=tip, kept=kept, squashed=total - kept)


def _replay(repo_dir: Path, sha: str, parent: str) -> str:
    """Re-create ``sha`` on top of ``parent``, reusing its tree object as-is."""
    who = _git.git(repo_dir, "log", "-1", "--format=%an%n%ae%n%aI", sha).splitlines()
    tree = _git.git(repo_dir, "rev-parse", f"{sha}^{{tree}}")
    body = _git.git(repo_dir, "log", "-1", "--format=%B", sha)
    return _git.git(
        repo_dir,
        "commit-tree",
        tree,
        "-p",
        parent,
        "-m",
        body,
        env={
            "GIT_AUTHOR_NAME": who[0],
            "GIT_AUTHOR_EMAIL": who[1],
            "GIT_AUTHOR_DATE": who[2],
        },
    )
