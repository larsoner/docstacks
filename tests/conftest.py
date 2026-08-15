"""Shared fixtures."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

#: Manifest of the seeded site repository: stable still points at 1.11, and the
#: legacy catch-all sits below the numbered entries where inserts must not go.
SITE_MANIFEST = """\
[
  {
    "name": "1.12 (dev)",
    "version": "dev",
    "url": "https://mne.tools/dev/"
  },
  {
    "name": "1.11 (stable)",
    "version": "1.11",
    "url": "https://mne.tools/stable/",
    "preferred": true
  },
  {
    "name": "≤ 0.20 (legacy)",
    "version": "legacy",
    "url": "https://mne.tools/dev/old_versions/"
  }
]
"""

#: Shaped after MNE-Python's real versions.json: a dev entry, a stable entry
#: served from the alias URL, an archived release, a hand-added foreign
#: catch-all, and an unknown key that must survive a round-trip.
MNE_MANIFEST = """\
[
  {
    "name": "1.13 (dev)",
    "version": "dev",
    "url": "https://mne.tools/dev/"
  },
  {
    "name": "1.12 (stable)",
    "version": "1.12",
    "url": "https://mne.tools/stable/",
    "preferred": true
  },
  {
    "name": "1.11",
    "version": "1.11",
    "url": "https://mne.tools/1.11/",
    "internal": "keep me"
  },
  {
    "name": "≤ 0.20 (legacy)",
    "version": "legacy",
    "url": "https://mne.tools/dev/old_versions/"
  }
]
"""


def _reindent(text: str, indent: str) -> str:
    lines = []
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip(" ")
        lines.append(indent * ((len(line) - len(stripped)) // 2) + stripped)
    return "".join(lines)


#: MNE's real versions.json is indented with four spaces, not two.
MNE_MANIFEST_4SPACE = _reindent(MNE_MANIFEST, "    ")


@pytest.fixture
def mne_manifest_text() -> str:
    """Realistic ``versions.json`` text."""
    return MNE_MANIFEST


@pytest.fixture
def mne_manifest_4space_text() -> str:
    """Realistic ``versions.json`` text, indented the way MNE ships it."""
    return MNE_MANIFEST_4SPACE


@pytest.fixture
def mne_manifest_path(tmp_path: Path) -> Path:
    """Realistic ``versions.json`` on disk."""
    path = tmp_path / "versions.json"
    path.write_text(MNE_MANIFEST, encoding="utf-8")
    return path


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo``, failing the test if it errors."""
    process = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return process.stdout.strip()


@pytest.fixture
def html_dir(tmp_path: Path) -> Path:
    """A freshly built HTML tree, as sphinx would leave it."""
    build = tmp_path / "build" / "html"
    (build / "_static").mkdir(parents=True)
    (build / "index.html").write_text("<html>1.12</html>", encoding="utf-8")
    (build / "_static" / "app.js").write_text("// 1.12\n", encoding="utf-8")
    return build


def add_commits(repo: Path, count: int) -> None:
    """Append ``count`` commits whose messages carry a trailer paragraph."""
    for index in range(count):
        (repo / f"note-{index}.txt").write_text(f"{index}\n", encoding="utf-8")
        git(repo, "add", "-A")
        git(repo, "commit", "-m", f"Note {index}\n\nDeployed-version: 0.{index}")


def init_repo(repo: Path) -> None:
    """Create an empty repository with an identity commits can be made under."""
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "docs@example.com")
    git(repo, "config", "user.name", "Docs Bot")
    git(repo, "config", "commit.gpgsign", "false")


@pytest.fixture
def site_repo(tmp_path: Path) -> Path:
    """A git checkout of a deployed site, with content docstacks must not touch."""
    repo = tmp_path / "site-repo"
    init_repo(repo)
    (repo / "CNAME").write_text("mne.tools\n", encoding="utf-8")
    (repo / ".nojekyll").touch()
    (repo / "index.html").write_text("<html>landing</html>", encoding="utf-8")
    (repo / "versions.json").write_text(SITE_MANIFEST, encoding="utf-8")
    for name, body in (("1.11", "1.11"), ("dev", "dev"), ("latest", "latest")):
        (repo / name).mkdir()
        (repo / name / "index.html").write_text(
            f"<html>{body}</html>", encoding="utf-8"
        )
    (repo / "1.11" / "style.css").write_text("body {}\n", encoding="utf-8")
    os.symlink("1.11", repo / "stable", target_is_directory=True)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "Seed the site")
    return repo


@pytest.fixture
def release_repo(tmp_path: Path) -> Path:
    """A site on the eve of a release: 1.12 is stable, 1.13 is about to land."""
    repo = tmp_path / "release-repo"
    init_repo(repo)
    (repo / "CNAME").write_text("mne.tools\n", encoding="utf-8")
    (repo / "index.html").write_text("<html>landing</html>", encoding="utf-8")
    (repo / "versions.json").write_text(MNE_MANIFEST, encoding="utf-8")
    for name in ("dev", "1.12", "1.11"):
        (repo / name).mkdir()
        (repo / name / "index.html").write_text(
            f"<html>{name}</html>", encoding="utf-8"
        )
    os.symlink("1.12", repo / "stable", target_is_directory=True)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "Seed the site")
    return repo


@pytest.fixture
def bare_remote(tmp_path: Path, site_repo: Path) -> Path:
    """A local bare repository wired up as ``origin`` of ``site_repo``."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-b", "main")
    git(site_repo, "remote", "add", "origin", str(remote))
    git(site_repo, "push", "origin", "main")
    return remote


@pytest.fixture
def site(tmp_path: Path) -> Path:
    """A deployed site tree with version dirs, a ``stable`` symlink, and junk."""
    site_dir = tmp_path / "site"
    site_dir.mkdir()
    for name in ("dev", "1.12", "1.11", "1.9", "0.25.x", "_images", "old_versions"):
        (site_dir / name).mkdir()
    os.symlink("1.12", site_dir / "stable", target_is_directory=True)
    (site_dir / "CNAME").write_text("mne.tools\n", encoding="utf-8")
    (site_dir / ".nojekyll").touch()
    (site_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    return site_dir
