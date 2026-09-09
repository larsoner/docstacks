"""Thin wrappers around the ``git`` command-line tool.

Shelling out keeps the runtime dependency set empty, which is a hard constraint
for this package; ``git`` is therefore a *tool* requirement rather than a
package one, and must be on ``PATH`` for anything in :mod:`docstacks.deploy` or
:mod:`docstacks.lifecycle`.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping

__all__ = ["GitError"]


class GitError(RuntimeError):
    """A git command exited nonzero."""


def git(
    repo_dir: str | os.PathLike[str],
    *args: str,
    env: Mapping[str, str] | None = None,
    stream: bool = False,
) -> str:
    """Run a git command inside ``repo_dir``.

    Parameters
    ----------
    repo_dir : path-like
        Directory to run in.
    *args : str
        Arguments to pass to ``git``.
    env : mapping | None
        Extra environment variables, layered over the current environment.
    stream : bool
        Let git write to the caller's stderr instead of capturing it, so a
        long-running command such as ``push`` shows its progress on a terminal.

    Returns
    -------
    output : str
        Stripped standard output.
    """
    process = _run(repo_dir, *args, env=env, stream=stream)
    if process.returncode != 0:
        detail = (process.stderr or process.stdout).strip()
        raise GitError(f"git {' '.join(args)} failed in {repo_dir}: {detail}")
    return process.stdout.strip()


def try_git(repo_dir: str | os.PathLike[str], *args: str) -> str | None:
    """Run a git command, treating a nonzero exit as an answer rather than a fault.

    Parameters
    ----------
    repo_dir : path-like
        Directory to run in.
    *args : str
        Arguments to pass to ``git``.

    Returns
    -------
    output : str | None
        Stripped standard output, or ``None`` when the command failed.
    """
    process = _run(repo_dir, *args)
    return process.stdout.strip() if process.returncode == 0 else None


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
    return try_git(repo_dir, "rev-parse", "--is-inside-work-tree") == "true"


def current_branch(repo_dir: str | os.PathLike[str]) -> str | None:
    """Name of the checked-out branch.

    Parameters
    ----------
    repo_dir : path-like
        Repository to inspect.

    Returns
    -------
    branch : str | None
        Short branch name, or ``None`` when ``HEAD`` is detached.
    """
    return try_git(repo_dir, "symbolic-ref", "--quiet", "--short", "HEAD")


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
    repo_dir: str | os.PathLike[str],
    *args: str,
    env: Mapping[str, str] | None = None,
    stream: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=os.fspath(repo_dir),
        stdout=subprocess.PIPE,
        stderr=None if stream else subprocess.PIPE,
        text=True,
        check=False,
        env=None if env is None else {**os.environ, **env},
    )
