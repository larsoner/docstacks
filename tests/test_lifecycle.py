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

#: A date well away from "now", so a lost committer date is obvious.
STAMP = "2020-01-02T03:04:05+02:00"


def history(repo: Path) -> list[tuple[str, str, str, str]]:
    """Tree, message, author, and committer of every commit, newest first."""
    return [
        (
            git(repo, "rev-parse", f"{sha}^{{tree}}"),
            git(repo, "log", "-1", "--format=%B", sha),
            git(repo, "log", "-1", "--format=%an <%ae> %aI", sha),
            git(repo, "log", "-1", "--format=%cn <%ce> %cI", sha),
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


def test_delete_manifest_only_entry(release_repo: Path) -> None:
    """An entry whose directory was never deployed is removed without a pathspec.

    Staging ``0.9`` here would hand git a pathspec matching nothing, which used
    to abort the command after the manifest had already been rewritten.
    """
    path = release_repo / "versions.json"
    Manifest.load(path).add("0.9", "https://mne.tools/0.9/").dump(path)
    git(release_repo, "commit", "-am", "List a version that was never deployed")

    sha = delete("0.9", release_repo)

    assert Manifest.load(path).get("0.9") is None
    assert [entry.version for entry in Manifest.load(path)] == [
        "dev",
        "1.12",
        "1.11",
        "legacy",
    ]
    body = git(release_repo, "log", "-1", "--format=%B", sha)
    assert body.splitlines() == ["Delete 0.9 docs", "", "Deleted-version: 0.9"]
    assert git(release_repo, "status", "--porcelain") == ""


def test_delete_refuses_an_untracked_directory(release_repo: Path) -> None:
    """A directory git ignores is not ours to remove, and nothing is touched.

    An ignored path leaves the working tree looking clean, so the clean-tree
    guard cannot catch it; refusing on "git tracks nothing here" is what keeps
    the operation from destroying an unmanaged directory it could not record.
    """
    (release_repo / ".gitignore").write_text("0.9/\n", encoding="utf-8")
    (release_repo / "0.9").mkdir()
    (release_repo / "0.9" / "index.html").write_text(
        "<html>0.9</html>", encoding="utf-8"
    )
    git(release_repo, "add", "-A")
    git(release_repo, "commit", "-m", "Ignore 0.9")
    assert git(release_repo, "status", "--porcelain") == ""

    with pytest.raises(DeployError, match="nothing to delete"):
        delete("0.9", release_repo)

    assert (release_repo / "0.9" / "index.html").is_file()
    assert git(release_repo, "status", "--porcelain") == ""


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


def test_prune_keeps_the_window_and_the_base(site_repo: Path) -> None:
    """The window survives byte-for-byte and the base keeps its own ancestry."""
    add_commits(site_repo, 5)
    base = git(site_repo, "rev-parse", "HEAD~4")
    before = history(site_repo)
    assert len(before) == 6

    result = prune(site_repo, keep=2, base=base)

    assert (result.kept, result.squashed, result.base) == (2, 2, base)
    assert result.branch == "main"
    assert result.tip == git(site_repo, "rev-parse", "HEAD")

    after = history(site_repo)
    assert len(after) == 5
    assert after[:2] == before[:2]
    assert after[2][0] == before[2][0]
    assert after[2][1] == SQUASH_SUBJECT
    assert after[3:] == before[4:]
    assert git(site_repo, "rev-parse", f"{result.tip}~3") == base
    assert git(site_repo, "status", "--porcelain") == ""
    assert (site_repo / "CNAME").read_text() == "mne.tools\n"


def test_prune_preserves_the_original_committer(
    site_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replayed commits keep their committer and date, not the pruner's clock."""
    base = git(site_repo, "rev-parse", "HEAD")
    stamped = {
        "GIT_AUTHOR_DATE": STAMP,
        "GIT_COMMITTER_DATE": STAMP,
        "GIT_COMMITTER_NAME": "Release Bot",
        "GIT_COMMITTER_EMAIL": "release@example.com",
    }
    for name, value in stamped.items():
        monkeypatch.setenv(name, value)
    add_commits(site_repo, 3)
    for name in stamped:
        monkeypatch.delenv(name)
    before = history(site_repo)

    prune(site_repo, keep=2, base=base)

    assert history(site_repo)[:2] == before[:2]
    assert (
        git(site_repo, "log", "-1", "--format=%cn <%ce> %cI", "HEAD")
        == f"Release Bot <release@example.com> {STAMP}"
    )


def test_prune_default_base_is_the_previous_squash(site_repo: Path) -> None:
    """A later run finds the commit the previous one squashed onto, by its subject."""
    base = git(site_repo, "rev-parse", "HEAD")
    add_commits(site_repo, 4)
    first = prune(site_repo, keep=2, base=base)
    squash = git(site_repo, "rev-parse", f"{first.tip}~2")
    add_commits(site_repo, 3)

    result = prune(site_repo, keep=2)

    assert (result.base, result.squashed, result.kept) == (squash, 3, 2)
    assert git(site_repo, "log", "-1", "--format=%s", squash) == SQUASH_SUBJECT
    assert git(site_repo, "rev-parse", f"{result.tip}~3") == squash
    assert git(site_repo, "rev-list", "--max-parents=0", "HEAD") == base


def test_prune_keep_since(site_repo: Path) -> None:
    """An explicit window revision behaves the same as counting back from HEAD."""
    base = git(site_repo, "rev-parse", "HEAD")
    add_commits(site_repo, 5)
    oldest = git(site_repo, "rev-parse", "HEAD~1")
    before = history(site_repo)

    result = prune(site_repo, keep_since=oldest, base=base)

    assert (result.kept, result.squashed) == (2, 3)
    assert history(site_repo)[:2] == before[:2]


@pytest.mark.parametrize("keep", [6, 99])
def test_prune_no_op_when_the_window_is_the_whole_history(
    site_repo: Path, keep: int
) -> None:
    """Asking to keep everything, or more than exists, rewrites nothing."""
    add_commits(site_repo, 5)
    before = history(site_repo)

    result = prune(site_repo, keep=keep)

    assert (result.kept, result.squashed, result.pending) == (6, 0, 0)
    assert result.base == git(site_repo, "rev-list", "--max-parents=0", "HEAD")
    assert result.tip == git(site_repo, "rev-parse", "HEAD")
    assert history(site_repo) == before


def test_prune_no_op_when_the_base_is_the_window_parent(site_repo: Path) -> None:
    """Nothing sits between the base and the window, so nothing is rewritten."""
    add_commits(site_repo, 5)
    base = git(site_repo, "rev-parse", "HEAD~2")
    before = history(site_repo)

    result = prune(site_repo, keep=2, base=base)

    assert (result.squashed, result.pending, result.base) == (0, 0, base)
    assert result.tip == git(site_repo, "rev-parse", "HEAD")
    assert history(site_repo) == before


def test_prune_min_squash_batches_the_rewrite(site_repo: Path) -> None:
    """Below the batch size nothing moves; reaching it collapses the backlog at once."""
    base = git(site_repo, "rev-parse", "HEAD")
    add_commits(site_repo, 4)
    before = history(site_repo)

    result = prune(site_repo, keep=2, base=base, min_squash=3)

    assert (result.squashed, result.pending, result.base) == (0, 2, base)
    assert history(site_repo) == before

    add_commits(site_repo, 1)
    result = prune(site_repo, keep=2, base=base, min_squash=3)

    assert (result.squashed, result.pending) == (3, 0)
    assert git(site_repo, "rev-parse", f"{result.tip}~3") == base


def test_prune_push(site_repo: Path, bare_remote: Path) -> None:
    """The rewritten branch is force-pushed with a lease."""
    base = git(site_repo, "rev-parse", "HEAD")
    add_commits(site_repo, 5)
    result = prune(site_repo, keep=2, base=base, push=True)
    assert git(bare_remote, "rev-parse", "main") == result.tip


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({}, "exactly one"),
        ({"keep": 2, "keep_since": "HEAD"}, "exactly one"),
        ({"keep": 0}, "at least 1"),
        ({"keep": 2, "min_squash": 0}, "min_squash must be at least 1"),
        ({"keep_since": "nope"}, "unknown revision"),
        ({"keep": 2, "base": "nope"}, "unknown revision"),
        ({"keep": 2, "base": "HEAD"}, "inside the window being kept"),
        ({"keep": 2}, "no earlier"),
    ],
)
def test_prune_refusals(site_repo: Path, kwargs: dict[str, Any], match: str) -> None:
    """A window or base that cannot be resolved into a rewrite is rejected."""
    add_commits(site_repo, 5)
    with pytest.raises(DeployError, match=match):
        prune(site_repo, **kwargs)


def test_prune_refuses_non_ancestor(site_repo: Path) -> None:
    """Neither the window nor the base may sit off to the side of HEAD."""
    add_commits(site_repo, 3)
    git(site_repo, "checkout", "-b", "side", "HEAD~2")
    (site_repo / "aside.txt").write_text("aside\n", encoding="utf-8")
    git(site_repo, "add", "-A")
    git(site_repo, "commit", "-m", "Off to the side")
    aside = git(site_repo, "rev-parse", "HEAD")
    git(site_repo, "checkout", "main")

    with pytest.raises(DeployError, match="not an ancestor"):
        prune(site_repo, keep_since=aside)
    with pytest.raises(DeployError, match="base .* is not an ancestor"):
        prune(site_repo, keep=2, base=aside)


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
