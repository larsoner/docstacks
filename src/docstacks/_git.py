"""Thin wrappers around the ``git`` command-line tool.

Shelling out keeps the runtime dependency set empty, which is a hard constraint
for this package; ``git`` is therefore a *tool* requirement rather than a
package one, and must be on ``PATH`` for anything in :mod:`docstacks.deploy`.
"""

from __future__ import annotations

import os
import subprocess

__all__ = ["GitError"]


class GitError(RuntimeError):
    """A git command exited nonzero."""


def git(repo_dir: str | os.PathLike[str], *args: str) -> str:
    """Run a git command inside ``repo_dir``.

    Parameters
    ----------
    repo_dir : path-like
        Directory to run in.
    *args : str
        Arguments to pass to ``git``.

    Returns
    -------
    output : str
        Stripped standard output.
    """
    process = _run(repo_dir, *args)
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip()
        raise GitError(f"git {' '.join(args)} failed in {repo_dir}: {detail}")
    return process.stdout.strip()


def is_worktree(repo_dir: str | os.PathLike[str]) -> bool:
    """Whether ``repo_dir`` is inside a git working tree.

    Parameters
    ----------
    repo_dir : path-like
        Directory to test.

    Returns
    -------
    inside : bool
        True when git considers the directory part of a working tree.
    """
    process = _run(repo_dir, "rev-parse", "--is-inside-work-tree")
    return process.returncode == 0 and process.stdout.strip() == "true"


def has_staged_changes(repo_dir: str | os.PathLike[str]) -> bool:
    """Whether anything is staged for commit.

    Parameters
    ----------
    repo_dir : path-like
        Repository to inspect.

    Returns
    -------
    staged : bool
        True when the index differs from ``HEAD``.
    """
    return _run(repo_dir, "diff", "--cached", "--quiet").returncode != 0


def _run(
    repo_dir: str | os.PathLike[str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=os.fspath(repo_dir),
        capture_output=True,
        text=True,
        check=False,
    )
