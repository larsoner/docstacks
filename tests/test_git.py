"""Tests for :mod:`docstacks._git`."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import git
from docstacks import _git


def test_git_reports_failures(tmp_path: Path) -> None:
    """A nonzero exit becomes a GitError carrying git's own message."""
    with pytest.raises(_git.GitError, match="rev-parse HEAD failed"):
        _git.git(tmp_path, "rev-parse", "HEAD")


def test_is_worktree(site_repo: Path, tmp_path: Path) -> None:
    """Only directories git considers part of a working tree qualify."""
    assert _git.is_worktree(site_repo)
    assert not _git.is_worktree(tmp_path)


def test_has_staged_changes(site_repo: Path) -> None:
    """Staged, but not merely modified, content counts."""
    assert not _git.has_staged_changes(site_repo)
    (site_repo / "CNAME").write_text("other.example\n", encoding="utf-8")
    assert not _git.has_staged_changes(site_repo)
    git(site_repo, "add", "CNAME")
    assert _git.has_staged_changes(site_repo)


def test_git_stream_passes_stderr_through(
    site_repo: Path, capfd: pytest.CaptureFixture
) -> None:
    """With ``stream`` git talks to the terminal itself, so stderr is not captured."""
    with pytest.raises(_git.GitError) as excinfo:
        _git.git(site_repo, "rev-parse", "--verify", "no-such-ref", stream=True)
    assert "fatal" not in str(excinfo.value)
    assert "fatal" in capfd.readouterr().err
