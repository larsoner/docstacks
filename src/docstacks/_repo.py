"""Guards and commit plumbing shared by every command that writes to a checkout.

Each command validates everything it can before the first byte is written, so a
refusal leaves the working tree exactly as it was found.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from docstacks import _git

__all__ = ["DeployError"]


class DeployError(RuntimeError):
    """A docstacks operation was refused, or could not be completed."""


def check_component(value: str, what: str) -> None:
    """Refuse anything that is not a single, benign path component."""
    if not value or value in (os.curdir, os.pardir) or {"/", "\\"} & set(value):
        raise DeployError(f"invalid {what} {value!r}: must be a single path component")


def check_repo(repo_dir: Path, *, push: bool) -> None:
    """Refuse a destination that is missing, not a checkout, dirty, or unpushable."""
    if not repo_dir.is_dir():
        raise DeployError(f"{repo_dir} is not a directory")
    if not _git.is_worktree(repo_dir):
        raise DeployError(f"{repo_dir} is not a git working tree")
    status = _git.git(repo_dir, "status", "--porcelain")
    if status:
        raise DeployError(
            f"{repo_dir} has uncommitted changes, refusing to continue:\n{status}"
        )
    if push:
        require_branch(repo_dir)


def require_branch(repo_dir: Path) -> str:
    """Name of the checked-out branch, refusing a detached ``HEAD``."""
    branch = _git.current_branch(repo_dir)
    if branch is None:
        raise DeployError(
            f"{repo_dir} has a detached HEAD; check out a branch before pushing"
        )
    return branch


def stage(repo_dir: Path, paths: Sequence[str]) -> None:
    """Stage exactly ``paths``, so unmanaged root content is never swept in."""
    _git.git(repo_dir, "add", "-A", "--", *paths)


def commit(repo_dir: Path, subject: str, trailers: str) -> str:
    """Commit the index as a subject paragraph plus a trailer paragraph.

    Separate ``-m`` paragraphs are used rather than ``git commit --trailer``,
    which is too new to rely on across CI images.
    """
    _git.git(repo_dir, "commit", "-m", subject, "-m", trailers)
    return _git.git(repo_dir, "rev-parse", "HEAD")


def push(repo_dir: Path, *, force_with_lease: bool = False) -> str:
    """Push the current branch to ``origin`` by name, never as bare ``HEAD``."""
    branch = require_branch(repo_dir)
    args = ["push"]
    if force_with_lease:
        args.append("--force-with-lease")
    _git.git(repo_dir, *args, "origin", branch)
    return branch
