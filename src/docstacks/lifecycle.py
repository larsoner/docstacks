"""Lifecycle operations on an already-deployed site: retitle, delete, prune.

None of these build or rebuild anything. ``retitle`` touches only the manifest,
``delete`` removes one version directory and its entry, and ``prune`` collapses
everything between a base commit and the retention window, without touching any
content.
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

#: Subject of the commit that replaces everything between the base and the window.
#: A later prune finds it again to use as that run's default base.
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
        Commits preserved, counting the oldest kept one.
    squashed : int
        Commits collapsed into the squash commit. Zero means nothing was
        rewritten.
    base : str
        Full SHA of the commit the squash was parented onto, whose own ancestry
        is untouched. With a ``squashed`` of zero it is whatever the preserved
        window already sat on.
    pending : int
        Commits sitting between the base and the window that were left there
        because ``min_squash`` was not reached yet.
    """

    branch: str
    tip: str
    kept: int
    squashed: int
    base: str
    pending: int = 0


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
    base: str | None = None,
    min_squash: int = 1,
    push: bool = False,
) -> PruneResult:
    """Collapse the history between a base commit and a retention window.

    Every commit from the oldest kept one to ``HEAD`` survives with its tree,
    message, author, and committer intact; everything between ``base``
    (exclusive) and that window becomes a single squash commit whose parent is
    ``base``. The rewrite is done with ``git commit-tree`` rather than a rebase:
    replaying diffs across multi-gigabyte documentation trees is hopeless, while
    re-parenting existing tree objects is cheap and cannot alter content.

    ``base`` and all of its ancestors are left exactly as they are, which is
    what keeps the push affordable. Git offers the remote a thin pack only for
    objects reachable from a commit both sides already have, so a history that
    shares no commit with the remote re-sends every tree and blob on the site:
    MNE's 6 GB tree squashed onto a parentless root produced a 2.59 GiB pack
    that GitHub refused at its 2 GiB limit, while the same push onto a kept base
    is around 400 MB.

    Parameters
    ----------
    repo_dir : path-like
        Checkout of the site repository. Must be a clean git working tree with a
        branch checked out.
    keep : int | None
        Number of commits to preserve, counting ``HEAD``. A history that short
        or shorter is left alone. Mutually exclusive with ``keep_since``.
    keep_since : str | None
        Oldest revision to preserve, kept along with everything after it.
    base : str | None
        Revision to squash onto, itself left untouched. Defaults to the most
        recent earlier commit a previous prune squashed onto; a history with
        none has to name one.
    min_squash : int
        Rewrite only once this many commits have piled up between the base and
        the window, so a job that runs after every deploy squashes in batches
        instead of adding a squash commit each time.
    push : bool
        Whether to ``git push --force-with-lease`` afterwards. This rewrites
        published history; every existing clone of the branch becomes invalid.

    Returns
    -------
    result : PruneResult
        Branch, new tip, base, and how many commits were kept and collapsed. A
        ``squashed`` of zero means nothing was rewritten, either because there
        is nothing between the base and the window or because ``min_squash``
        has not been reached.
    """
    repo_path = Path(repo_dir)
    if (keep is None) == (keep_since is None):
        raise DeployError("pass exactly one of keep or keep_since")
    if min_squash < 1:
        raise DeployError(f"min_squash must be at least 1, got {min_squash}")
    check_repo(repo_path, push=push)
    branch = require_branch(repo_path)

    total = int(_git.git(repo_path, "rev-list", "--count", "HEAD"))
    oldest = keep_since
    if keep is not None:
        if keep < 1:
            raise DeployError(f"keep must be at least 1, got {keep}")
        # a short history is nothing to collapse, so a CI job can run this every deploy
        oldest = f"HEAD~{min(keep, total) - 1}"

    oldest_sha = _git.try_git(
        repo_path, "rev-parse", "--verify", "--quiet", f"{oldest}^{{commit}}"
    )
    if oldest_sha is None:
        raise DeployError(f"unknown revision {oldest!r}")
    if (
        _git.try_git(repo_path, "merge-base", "--is-ancestor", oldest_sha, "HEAD")
        is None
    ):
        raise DeployError(f"{oldest!r} is not an ancestor of HEAD")

    kept = int(_git.git(repo_path, "rev-list", "--count", f"{oldest_sha}..HEAD")) + 1
    parent = _git.try_git(
        repo_path, "rev-parse", "--verify", "--quiet", f"{oldest_sha}^"
    )
    if parent is None:
        head = _git.git(repo_path, "rev-parse", "HEAD")
        return PruneResult(
            branch=branch, tip=head, kept=kept, squashed=0, base=oldest_sha
        )

    base_sha = _find_base(repo_path, base, parent)
    if _git.try_git(repo_path, "merge-base", "--is-ancestor", base_sha, "HEAD") is None:
        raise DeployError(f"base {base_sha} is not an ancestor of HEAD")
    if _git.try_git(repo_path, "merge-base", "--is-ancestor", base_sha, parent) is None:
        raise DeployError(
            f"base {base_sha} is inside the window being kept; it has to be "
            "older than every preserved commit"
        )

    # a base already at the window's edge lands here as zero, the same no-op
    squashed = int(_git.git(repo_path, "rev-list", "--count", f"{base_sha}..{parent}"))
    if squashed < min_squash:
        head = _git.git(repo_path, "rev-parse", "HEAD")
        return PruneResult(
            branch=branch,
            tip=head,
            kept=kept,
            squashed=0,
            base=base_sha,
            pending=squashed,
        )
    tree = _git.git(repo_path, "rev-parse", f"{parent}^{{tree}}")
    tip = _git.git(repo_path, "commit-tree", tree, "-p", base_sha, "-m", SQUASH_SUBJECT)
    replayed = _git.git(repo_path, "rev-list", "--reverse", f"{oldest_sha}^..HEAD")
    for sha in replayed.splitlines():
        tip = _replay(repo_path, sha, tip)

    _git.git(repo_path, "update-ref", f"refs/heads/{branch}", tip)
    _git.git(repo_path, "reset", "--hard")
    if push:
        push_branch(repo_path, force_with_lease=True)
    return PruneResult(
        branch=branch, tip=tip, kept=kept, squashed=squashed, base=base_sha
    )


def _find_base(repo_dir: Path, base: str | None, parent: str) -> str:
    """Resolve the commit to squash onto, defaulting to the last prune's."""
    if base is not None:
        sha = _git.try_git(
            repo_dir, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}"
        )
        if sha is None:
            raise DeployError(f"unknown revision {base!r}")
        return sha
    for line in _git.git(repo_dir, "log", "--format=%H %s", parent).splitlines():
        sha, _, subject = line.partition(" ")
        if subject == SQUASH_SUBJECT:
            return sha
    raise DeployError(
        f"no earlier {SQUASH_SUBJECT!r} commit to squash onto; pass --base REV "
        "naming the commit to keep, which every later prune then finds itself"
    )


def _replay(repo_dir: Path, sha: str, parent: str) -> str:
    """Re-create ``sha`` on top of ``parent``, reusing its tree object as-is."""
    fields = (
        "GIT_AUTHOR_NAME",
        "GIT_AUTHOR_EMAIL",
        "GIT_AUTHOR_DATE",
        "GIT_COMMITTER_NAME",
        "GIT_COMMITTER_EMAIL",
        "GIT_COMMITTER_DATE",
    )
    who = _git.git(
        repo_dir, "log", "-1", "--format=%an%n%ae%n%aI%n%cn%n%ce%n%cI", sha
    ).splitlines()
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
        env=dict(zip(fields, who, strict=True)),
    )
