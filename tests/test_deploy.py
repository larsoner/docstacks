"""Tests for :mod:`docstacks.deploy`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import git
from docstacks.deploy import DeployError, deploy, promote
from docstacks.manifest import Manifest

BASE_URL = "https://mne.tools/"

#: Root content docstacks does not own and must never disturb.
UNMANAGED = (
    "CNAME",
    ".nojekyll",
    "index.html",
    "dev/index.html",
    "latest/index.html",
)


def snapshot(repo: Path) -> dict[str, bytes]:
    """Bytes of every root file docstacks is not responsible for."""
    return {name: (repo / name).read_bytes() for name in UNMANAGED}


def test_deploy_new_version(html_dir: Path, site_repo: Path) -> None:
    """A fresh version lands as a directory, an entry, and a commit with trailers."""
    before = snapshot(site_repo)
    sha = deploy(html_dir, "1.12", site_repo, base_url=BASE_URL, source_sha="cafe1234")

    assert sha == git(site_repo, "rev-parse", "HEAD")
    assert (site_repo / "1.12" / "index.html").read_text() == "<html>1.12</html>"
    assert (site_repo / "1.12" / "_static" / "app.js").is_file()
    assert snapshot(site_repo) == before
    assert os.readlink(site_repo / "stable") == "1.11"
    assert git(site_repo, "status", "--porcelain") == ""

    manifest = Manifest.load(site_repo / "versions.json")
    assert [entry.version for entry in manifest] == ["dev", "1.12", "1.11", "legacy"]
    entry = manifest.entries[1]
    assert entry.url == "https://mne.tools/1.12/"
    assert entry.name is None
    assert not entry.preferred

    body = git(site_repo, "log", "-1", "--format=%B")
    assert body.splitlines()[:4] == [
        "Deploy 1.12 docs",
        "",
        "Deployed-version: 1.12",
        "Source-sha: cafe1234",
    ]


def test_deploy_custom_message_without_source_sha(
    html_dir: Path, site_repo: Path
) -> None:
    """The subject is overridable and the Source-sha trailer is optional."""
    deploy(html_dir, "1.12", site_repo, base_url=BASE_URL, message="Ship 1.12")
    body = git(site_repo, "log", "-1", "--format=%B")
    assert body.splitlines()[:3] == ["Ship 1.12", "", "Deployed-version: 1.12"]
    assert "Source-sha" not in body


@pytest.mark.parametrize("relabel", [None, "1.11 LTS"])
def test_deploy_redeploy_keeps_the_alias_it_already_has(
    html_dir: Path, site_repo: Path, relabel: str | None
) -> None:
    """Stale files go, the entry is updated rather than duplicated, its base URL
    comes from the URL already recorded for it, and the ``stable`` it already
    carries keeps supplying that URL without being repeated or rewritten -- and
    without relabeling an entry that was retitled by hand."""
    path = site_repo / "versions.json"
    if relabel is not None:
        Manifest.load(path).retitle("1.11", relabel).dump(path)
        git(site_repo, "commit", "-am", "Hand-label 1.11")

    deploy(html_dir, "1.11", site_repo)

    assert not (site_repo / "1.11" / "style.css").exists()
    assert (site_repo / "1.11" / "index.html").read_text() == "<html>1.12</html>"
    assert os.readlink(site_repo / "stable") == "1.11"
    manifest = Manifest.load(path)
    assert [entry.version for entry in manifest] == ["dev", "1.11", "legacy"]
    assert manifest.entries[1].url == "https://mne.tools/stable/"
    assert manifest.entries[1].name == (relabel or "1.11 (stable)")
    assert manifest.entries[1].preferred


def test_deploy_redeploy_unaliased_version(html_dir: Path, site_repo: Path) -> None:
    """A version nothing points at still goes back to its own directory URL."""
    deploy(html_dir, "dev", site_repo)

    manifest = Manifest.load(site_repo / "versions.json")
    entry = manifest.entries[0]
    assert (entry.version, entry.url, entry.name) == (
        "dev",
        "https://mne.tools/dev/",
        "1.12 (dev)",
    )
    assert [item.version for item in manifest if item.preferred] == ["1.11"]


def test_deploy_alongside_an_existing_alias(html_dir: Path, site_repo: Path) -> None:
    """A new alias joins the one already there, which still supplies the URL."""
    deploy(html_dir, "1.11", site_repo, aliases=("current",))

    assert os.readlink(site_repo / "current") == "1.11"
    assert os.readlink(site_repo / "stable") == "1.11"
    entry = Manifest.load(site_repo / "versions.json").get("1.11")
    assert entry is not None
    assert entry.url == "https://mne.tools/stable/"


def test_deploy_keeps_an_alias_chain(html_dir: Path, site_repo: Path) -> None:
    """pandas-style ``stable -> 2.1 -> 2.1.3`` counts, and is not flattened."""
    (site_repo / "stable").unlink()
    (site_repo / "2.1.3").mkdir()
    (site_repo / "2.1.3" / "index.html").write_text(
        "<html>2.1.3</html>", encoding="utf-8"
    )
    os.symlink("2.1.3", site_repo / "2.1", target_is_directory=True)
    os.symlink("2.1", site_repo / "stable", target_is_directory=True)
    git(site_repo, "add", "-A")
    git(site_repo, "commit", "-m", "Chain stable at 2.1.3")

    deploy(html_dir, "2.1.3", site_repo, base_url=BASE_URL)

    assert os.readlink(site_repo / "stable") == "2.1"
    assert os.readlink(site_repo / "2.1") == "2.1.3"
    entry = Manifest.load(site_repo / "versions.json").get("2.1.3")
    assert entry is not None
    # the inherited alias decides the URL and the flag, but never writes a label
    assert (entry.url, entry.name, entry.preferred) == (
        "https://mne.tools/stable/",
        None,
        True,
    )


def test_deploy_relabels_existing_entry(html_dir: Path, site_repo: Path) -> None:
    """An explicit name replaces the generated one, even on an aliased version, and
    base URLs need no trailing slash."""
    deploy(
        html_dir,
        "1.11",
        site_repo,
        base_url="https://mne.tools",
        name="1.11 (archived)",
    )
    entry = Manifest.load(site_repo / "versions.json").get("1.11")
    assert entry is not None
    assert entry.name == "1.11 (archived)"
    assert entry.url == "https://mne.tools/stable/"


def test_deploy_retargets_alias(html_dir: Path, site_repo: Path) -> None:
    """``stable`` moves from 1.11 to 1.12, taking the preferred flag with it."""
    deploy(html_dir, "1.12", site_repo, aliases=("stable",), base_url=BASE_URL)

    assert os.readlink(site_repo / "stable") == "1.12"
    assert (site_repo / "1.11" / "index.html").read_text() == "<html>1.11</html>"
    manifest = Manifest.load(site_repo / "versions.json")
    new, old = manifest.entries[1], manifest.entries[2]
    assert (new.version, new.url, new.name) == (
        "1.12",
        "https://mne.tools/stable/",
        "1.12 (stable)",
    )
    assert new.preferred
    assert not old.preferred
    assert manifest.validate() == []


def test_deploy_creates_new_alias(html_dir: Path, site_repo: Path) -> None:
    """Several aliases can point at one version; stable supplies the URL."""
    deploy(
        html_dir, "1.12", site_repo, aliases=("current", "stable"), base_url=BASE_URL
    )
    assert os.readlink(site_repo / "current") == "1.12"
    assert os.readlink(site_repo / "stable") == "1.12"
    entry = Manifest.load(site_repo / "versions.json").get("1.12")
    assert entry is not None
    assert entry.url == "https://mne.tools/stable/"


def test_deploy_over_symlinked_version(html_dir: Path, site_repo: Path) -> None:
    """A version directory that is currently a symlink becomes a real directory."""
    os.symlink("1.11", site_repo / "1.10", target_is_directory=True)
    git(site_repo, "add", "-A")
    git(site_repo, "commit", "-m", "Alias 1.10 at 1.11")

    deploy(html_dir, "1.10", site_repo, base_url=BASE_URL)

    assert not (site_repo / "1.10").is_symlink()
    assert (site_repo / "1.10" / "index.html").read_text() == "<html>1.12</html>"
    manifest = Manifest.load(site_repo / "versions.json")
    assert [entry.version for entry in manifest] == ["dev", "1.11", "1.10", "legacy"]


def test_deploy_creates_missing_manifest(html_dir: Path, site_repo: Path) -> None:
    """A site with no versions.json yet gets one."""
    (site_repo / "versions.json").unlink()
    git(site_repo, "commit", "-am", "Drop the manifest")

    deploy(html_dir, "1.12", site_repo, aliases=("stable",), base_url=BASE_URL)

    manifest = Manifest.load(site_repo / "versions.json")
    assert [entry.version for entry in manifest] == ["1.12"]
    assert manifest.indent == 2


def test_deploy_preserves_manifest_indent(html_dir: Path, site_repo: Path) -> None:
    """Updating a 4-space manifest does not reindent it."""
    path = site_repo / "versions.json"
    path.write_text(Manifest.load(path).dumps().replace("  ", "    "), encoding="utf-8")
    git(site_repo, "commit", "-am", "Reindent the manifest")

    deploy(html_dir, "1.12", site_repo, base_url=BASE_URL)

    assert Manifest.load(path).indent == 4


def test_deploy_without_manifest(html_dir: Path, site_repo: Path) -> None:
    """``manifest_path=None`` deploys content only."""
    before = (site_repo / "versions.json").read_bytes()
    deploy(html_dir, "1.12", site_repo, manifest_path=None)
    assert (site_repo / "versions.json").read_bytes() == before
    assert (site_repo / "1.12" / "index.html").is_file()
    assert git(site_repo, "status", "--porcelain") == ""


def test_deploy_push(html_dir: Path, site_repo: Path, bare_remote: Path) -> None:
    """``push=True`` sends the new commit to origin."""
    sha = deploy(html_dir, "1.12", site_repo, base_url=BASE_URL, push=True)
    assert git(bare_remote, "rev-parse", "main") == sha


def test_deploy_refuses_alias_over_real_directory(
    html_dir: Path, site_repo: Path
) -> None:
    """A real directory is never deleted to make room for an alias."""
    with pytest.raises(DeployError, match="not a symlink"):
        deploy(html_dir, "1.12", site_repo, aliases=("latest",), base_url=BASE_URL)
    assert not (site_repo / "1.12").exists()
    assert git(site_repo, "status", "--porcelain") == ""


def test_deploy_refuses_dirty_tree(html_dir: Path, site_repo: Path) -> None:
    """An uncommitted change anywhere in the checkout stops the deploy."""
    (site_repo / "CNAME").write_text("other.example\n", encoding="utf-8")
    with pytest.raises(DeployError, match="uncommitted changes"):
        deploy(html_dir, "1.12", site_repo, base_url=BASE_URL)


def test_deploy_refuses_empty_build(tmp_path: Path, site_repo: Path) -> None:
    """The classic empty-build guard, plus a missing directory."""
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(DeployError, match="no index.html"):
        deploy(empty, "1.12", site_repo, base_url=BASE_URL)
    with pytest.raises(DeployError, match="is not a directory"):
        deploy(tmp_path / "absent", "1.12", site_repo, base_url=BASE_URL)


@pytest.mark.parametrize("version", ["", ".", "..", "1.12/html", "a\\b"])
def test_deploy_refuses_bad_version(
    html_dir: Path, site_repo: Path, version: str
) -> None:
    """Version names must be a single path component."""
    with pytest.raises(DeployError, match="single path component"):
        deploy(html_dir, version, site_repo, base_url=BASE_URL)


def test_deploy_refuses_bad_alias(html_dir: Path, site_repo: Path) -> None:
    """Alias names are held to the same standard, and cannot self-reference."""
    with pytest.raises(DeployError, match="single path component"):
        deploy(html_dir, "1.12", site_repo, aliases=("a/b",), base_url=BASE_URL)
    with pytest.raises(DeployError, match="same as the version"):
        deploy(html_dir, "1.12", site_repo, aliases=("1.12",), base_url=BASE_URL)


def test_deploy_refuses_non_repository(html_dir: Path, tmp_path: Path) -> None:
    """The destination has to be a git working tree that exists."""
    with pytest.raises(DeployError, match="not a git working tree"):
        deploy(html_dir, "1.12", tmp_path, base_url=BASE_URL)
    with pytest.raises(DeployError, match="is not a directory"):
        deploy(html_dir, "1.12", tmp_path / "absent", base_url=BASE_URL)


def test_deploy_refuses_absolute_manifest_path(
    html_dir: Path, site_repo: Path, tmp_path: Path
) -> None:
    """The manifest is addressed relative to the checkout."""
    with pytest.raises(DeployError, match="must be relative"):
        deploy(
            html_dir,
            "1.12",
            site_repo,
            base_url=BASE_URL,
            manifest_path=str(tmp_path / "versions.json"),
        )


def test_deploy_requires_base_url_for_new_entry(
    html_dir: Path, site_repo: Path
) -> None:
    """Nothing in the manifest can supply a URL for a version it has never seen."""
    with pytest.raises(DeployError, match="base_url is required"):
        deploy(html_dir, "1.12", site_repo)


def test_deploy_reports_uninferable_base_url(html_dir: Path, site_repo: Path) -> None:
    """A URL with no path segment to strip is not a usable base."""
    path = site_repo / "versions.json"
    manifest = Manifest.load(path)
    entry = manifest.get("1.11")
    assert entry is not None
    entry.url = "https://mne.tools/"
    manifest.dump(path)
    git(site_repo, "commit", "-am", "Flatten a URL")

    with pytest.raises(DeployError, match="cannot infer base_url"):
        deploy(html_dir, "1.11", site_repo)


def test_deploy_refuses_no_op(html_dir: Path, site_repo: Path) -> None:
    """Deploying byte-identical content twice is an error, not an empty commit."""
    deploy(html_dir, "1.12", site_repo, base_url=BASE_URL)
    with pytest.raises(DeployError, match="nothing to deploy"):
        deploy(html_dir, "1.12", site_repo, base_url=BASE_URL)

    # an aliased version is the case where the manifest used to change on its own
    deploy(html_dir, "1.11", site_repo)
    with pytest.raises(DeployError, match="nothing to deploy"):
        deploy(html_dir, "1.11", site_repo)


def test_deploy_refuses_detached_head_before_writing(
    html_dir: Path, site_repo: Path
) -> None:
    """A push that cannot work is caught before the deploy, not after committing."""
    head = git(site_repo, "rev-parse", "HEAD")
    git(site_repo, "checkout", "--detach")
    with pytest.raises(DeployError, match="detached HEAD"):
        deploy(html_dir, "1.12", site_repo, base_url=BASE_URL, push=True)
    assert not (site_repo / "1.12").exists()
    assert git(site_repo, "rev-parse", "HEAD") == head


def test_promote_release_day(html_dir: Path, release_repo: Path) -> None:
    """The whole release-day transaction, in a single commit.

    The outgoing stable must stop claiming the /stable/ URL the moment the
    symlink moves, or the manifest tells every visitor a lie.
    """
    before = int(git(release_repo, "rev-list", "--count", "HEAD"))
    sha = promote(
        html_dir, "1.13", release_repo, base_url=BASE_URL, source_sha="cafe1234"
    )

    assert int(git(release_repo, "rev-list", "--count", "HEAD")) == before + 1
    assert os.readlink(release_repo / "stable") == "1.13"
    assert (release_repo / "1.13" / "index.html").read_text() == "<html>1.12</html>"
    assert (release_repo / "1.12" / "index.html").read_text() == "<html>1.12</html>"

    manifest = Manifest.load(release_repo / "versions.json")
    assert [
        (entry.version, entry.name, entry.url, entry.preferred) for entry in manifest
    ] == [
        ("dev", "1.13 (dev)", "https://mne.tools/dev/", False),
        ("1.13", "1.13 (stable)", "https://mne.tools/stable/", True),
        ("1.12", None, "https://mne.tools/1.12/", False),
        ("1.11", "1.11", "https://mne.tools/1.11/", False),
        ("legacy", "≤ 0.20 (legacy)", "https://mne.tools/dev/old_versions/", False),
    ]
    assert manifest.entries[3].extra == {"internal": "keep me"}
    assert manifest.validate() == []

    body = git(release_repo, "log", "-1", "--format=%B", sha)
    assert body.splitlines()[:4] == [
        "Promote 1.13 to stable",
        "",
        "Deployed-version: 1.13",
        "Source-sha: cafe1234",
    ]


def test_promote_keeps_a_hand_written_name(html_dir: Path, release_repo: Path) -> None:
    """A custom label survives demotion; only the URL it implies is corrected."""
    path = release_repo / "versions.json"
    Manifest.load(path).retitle("1.12", "1.12 LTS").dump(path)
    git(release_repo, "commit", "-am", "Hand-label 1.12")

    promote(html_dir, "1.13", release_repo, base_url=BASE_URL)

    entry = Manifest.load(path).get("1.12")
    assert entry is not None
    assert entry.name == "1.12 LTS"
    assert entry.url == "https://mne.tools/1.12/"


def test_promote_without_an_incumbent(html_dir: Path, release_repo: Path) -> None:
    """With nothing preferred and no stable URL in use, nothing gets demoted."""
    path = release_repo / "versions.json"
    manifest = Manifest.load(path)
    entry = manifest.get("1.12")
    assert entry is not None
    entry.preferred, entry.name, entry.url = False, None, "https://mne.tools/1.12/"
    manifest.dump(path)
    (release_repo / "stable").unlink()
    git(release_repo, "add", "-A")
    git(release_repo, "commit", "-m", "No stable yet")

    promote(html_dir, "1.13", release_repo, base_url=BASE_URL)

    manifest = Manifest.load(path)
    assert [entry.version for entry in manifest if entry.preferred] == ["1.13"]
    untouched = manifest.get("1.12")
    assert untouched is not None
    assert (untouched.name, untouched.url) == (None, "https://mne.tools/1.12/")


def test_promote_custom_alias_and_message(html_dir: Path, release_repo: Path) -> None:
    """Promoting under a different alias still demotes the entry it displaced."""
    promote(
        html_dir,
        "1.13",
        release_repo,
        aliases=("stable", "current"),
        base_url=BASE_URL,
        message="Release 1.13",
    )
    assert os.readlink(release_repo / "current") == "1.13"
    entry = Manifest.load(release_repo / "versions.json").get("1.13")
    assert entry is not None
    assert entry.url == "https://mne.tools/stable/"
    assert git(release_repo, "log", "-1", "--format=%s") == "Release 1.13"
