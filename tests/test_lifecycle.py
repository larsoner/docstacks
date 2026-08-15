"""Tests for :mod:`docstacks.lifecycle`."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from conftest import add_commits, git
from docstacks._repo import DeployError
from docstacks.lifecycle import SQUASH_SUBJECT, delete, prune, retitle
from docstacks.manifest import Manifest


def history(repo: Path) -> list[tuple[str, str, str]]:
    """Tree, full message, and author of every commit, newest first."""
    return [
        (
            git(repo, "rev-parse", f"{sha}^{{tree}}"),
            git(repo, "log", "-1", "--format=%B", sha),
            git(repo, "log", "-1", "--format=%an <%ae> %aI", sha),
        )
        for sha in git(repo, "rev-list", "HEAD").splitlines()
    ]


# -- retitle ---------------------------------------------------------------


def test_retitle_sets_and_clears(site_repo: Path) -> None:
    """A label can be replaced and then removed, each as its own commit."""
    path = site_repo / "versions.json"
    sha = retitle("1.11", "1.11 (archived)", site_repo)

    entry = Manifest.load(path).get("1.11")
    assert entry is not None
    assert entry.name == "1.11 (archived)"
    body = git(site_repo, "log", "-1", "--format=%B", sha)
    assert body.splitlines() == ["Retitle 1.11", "", "Retitled-version: 1.11"]

    retitle("1.11", "", site_repo)
    entry = Manifest.load(path).get("1.11")
    assert entry is not None
    assert entry.name is None
    assert entry.display_name == "1.11"
    assert git(site_repo, "status", "--porcelain") == ""


def test_retitle_refusals(site_repo: Path, tmp_path: Path) -> None:
    """Unknown versions, missing manifests, and no-ops are all refused."""
    with pytest.raises(DeployError, match="no entry for '9.9'"):
        retitle("9.9", "nope", site_repo)
    with pytest.raises(DeployError, match="nothing to change"):
        retitle("1.11", "1.11 (stable)", site_repo)
    (site_repo / "versions.json").unlink()
    git(site_repo, "commit", "-am", "Drop the manifest")
    with pytest.raises(DeployError, match="does not exist"):
        retitle("1.11", "nope", site_repo)


# -- delete ----------------------------------------------------------------


def test_retitle_and_delete_push(site_repo: Path, bare_remote: Path) -> None:
    """Both manifest-lifecycle commands can publish the commit they make."""
    sha = retitle("1.11", "1.11 (archived)", site_repo, push=True)
    assert git(bare_remote, "rev-parse", "main") == sha
    sha = delete("latest", site_repo, push=True)
    assert git(bare_remote, "rev-parse", "main") == sha


def test_delete(release_repo: Path) -> None:
    """An unaliased, non-preferred version goes away cleanly."""
    sha = delete("1.11", release_repo)

    assert not (release_repo / "1.11").exists()
    assert (release_repo / "1.12" / "index.html").is_file()
    assert (release_repo / "CNAME").read_text() == "mne.tools\n"
    manifest = Manifest.load(release_repo / "versions.json")
    assert [entry.version for entry in manifest] == ["dev", "1.12", "legacy"]
    body = git(release_repo, "log", "-1", "--format=%B", sha)
    assert body.splitlines() == ["Delete 1.11 docs", "", "Deleted-version: 1.11"]
    assert git(release_repo, "status", "--porcelain") == ""


def test_delete_untracked_by_the_manifest(site_repo: Path) -> None:
    """A root directory with no manifest entry can still be removed."""
    before = (site_repo / "versions.json").read_bytes()
    delete("latest", site_repo)
    assert not (site_repo / "latest").exists()
    assert (site_repo / "versions.json").read_bytes() == before


def test_delete_symlinked_version(site_repo: Path) -> None:
    """Deleting a version that is itself a symlink unlinks rather than recurses."""
    os.symlink("1.11", site_repo / "1.10", target_is_directory=True)
    git(site_repo, "add", "-A")
    git(site_repo, "commit", "-m", "Alias 1.10 at 1.11")

    delete("1.10", site_repo)

    assert not os.path.lexists(site_repo / "1.10")
    assert (site_repo / "1.11" / "index.html").is_file()


def test_delete_refuses_aliased_version(release_repo: Path) -> None:
    """A version something still points at cannot be removed."""
    os.symlink("1.11", release_repo / "previous", target_is_directory=True)
    git(release_repo, "add", "-A")
    git(release_repo, "commit", "-m", "Alias previous at 1.11")

    with pytest.raises(DeployError, match="still aliased by previous"):
        delete("1.11", release_repo)
    assert (release_repo / "1.11" / "index.html").is_file()
    assert git(release_repo, "status", "--porcelain") == ""


def test_delete_refuses_preferred_version(release_repo: Path) -> None:
    """The preferred version has to be replaced, not removed."""
    with pytest.raises(DeployError, match="is the preferred version"):
        delete("1.12", release_repo)


def test_delete_refuses_unknown_version(release_repo: Path) -> None:
    """There has to be something there to delete."""
    with pytest.raises(DeployError, match="nothing to delete"):
        delete("9.9", release_repo)


# -- prune -----------------------------------------------------------------


def test_prune_keeps_anchor_through_tip(site_repo: Path) -> None:
    """Everything from the anchor up survives byte-for-byte; the rest collapses."""
    add_commits(site_repo, 5)
    before = history(site_repo)
    assert len(before) == 6

    result = prune(site_repo, keep=2)

    assert (result.kept, result.squashed) == (2, 4)
    assert result.branch == "main"
    assert result.tip == git(site_repo, "rev-parse", "HEAD")

    after = history(site_repo)
    assert len(after) == 3
    assert after[:2] == before[:2]
    assert after[2][0] == before[2][0]
    assert after[2][1] == SQUASH_SUBJECT
    assert git(site_repo, "rev-list", "--max-parents=0", "HEAD") == git(
        site_repo, "rev-parse", f"{result.tip}~2"
    )
    assert git(site_repo, "status", "--porcelain") == ""
    assert (site_repo / "CNAME").read_text() == "mne.tools\n"


def test_prune_keep_since(site_repo: Path) -> None:
    """An explicit anchor revision behaves the same as counting back from HEAD."""
    add_commits(site_repo, 5)
    anchor = git(site_repo, "rev-parse", "HEAD~1")
    before = history(site_repo)

    result = prune(site_repo, keep_since=anchor)

    assert (result.kept, result.squashed) == (2, 4)
    assert history(site_repo)[:2] == before[:2]


def test_prune_no_op_when_anchor_is_root(site_repo: Path) -> None:
    """Asking to keep everything rewrites nothing."""
    add_commits(site_repo, 5)
    before = history(site_repo)

    result = prune(site_repo, keep=6)

    assert (result.kept, result.squashed) == (6, 0)
    assert result.tip == git(site_repo, "rev-parse", "HEAD")
    assert history(site_repo) == before


def test_prune_push(site_repo: Path, bare_remote: Path) -> None:
    """The rewritten branch is force-pushed with a lease."""
    add_commits(site_repo, 5)
    result = prune(site_repo, keep=2, push=True)
    assert git(bare_remote, "rev-parse", "main") == result.tip


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({}, "exactly one"),
        ({"keep": 2, "keep_since": "HEAD"}, "exactly one"),
        ({"keep": 0}, "at least 1"),
        ({"keep": 99}, "has only 6"),
        ({"keep_since": "nope"}, "unknown revision"),
    ],
)
def test_prune_refusals(site_repo: Path, kwargs: dict[str, Any], match: str) -> None:
    """Anchors that cannot be resolved into a rewrite are rejected up front."""
    add_commits(site_repo, 5)
    with pytest.raises(DeployError, match=match):
        prune(site_repo, **kwargs)


def test_prune_refuses_non_ancestor(site_repo: Path) -> None:
    """An anchor off to the side of HEAD is not a retention point."""
    add_commits(site_repo, 3)
    git(site_repo, "checkout", "-b", "side", "HEAD~2")
    (site_repo / "aside.txt").write_text("aside\n", encoding="utf-8")
    git(site_repo, "add", "-A")
    git(site_repo, "commit", "-m", "Off to the side")
    aside = git(site_repo, "rev-parse", "HEAD")
    git(site_repo, "checkout", "main")

    with pytest.raises(DeployError, match="not an ancestor"):
        prune(site_repo, keep_since=aside)


def test_prune_refuses_dirty_tree(site_repo: Path) -> None:
    """History is never rewritten out from under uncommitted work."""
    (site_repo / "CNAME").write_text("other.example\n", encoding="utf-8")
    with pytest.raises(DeployError, match="uncommitted changes"):
        prune(site_repo, keep=1)


def test_prune_refuses_detached_head(site_repo: Path) -> None:
    """There is no branch to move, so there is nothing to prune."""
    add_commits(site_repo, 2)
    git(site_repo, "checkout", "--detach")
    with pytest.raises(DeployError, match="detached HEAD"):
        prune(site_repo, keep=2)
