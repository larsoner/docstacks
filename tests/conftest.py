"""Shared fixtures."""

from __future__ import annotations

import functools
import os
import subprocess
import threading
import time
from collections.abc import Iterator
from http.server import (
    BaseHTTPRequestHandler,
    SimpleHTTPRequestHandler,
    ThreadingHTTPServer,
)
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
    # a second call has to keep numbering where the first stopped, or its
    # identical files stage nothing and git refuses the commit
    start = len(list(repo.glob("note-*.txt")))
    for index in range(start, start + count):
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


#: Seconds the slow handler stalls for; only ever hit with a much smaller timeout.
STALL_SECONDS = 30.0

#: Page shape pydata-sphinx-theme produces, trimmed to what the check looks at.
PAGE = """<!DOCTYPE html>
<html><head><script id="documentation_options">
const DOCUMENTATION_OPTIONS = {{}};
DOCUMENTATION_OPTIONS.theme_switcher_version_match = '{match}';
</script></head><body>docs</body></html>
"""


class QuietHandler(SimpleHTTPRequestHandler):
    """Static file handler that records paths instead of logging them."""

    #: Every path served since the last reset, for one-fetch-per-entry checks.
    requests: list[str] = []

    def do_GET(self) -> None:
        QuietHandler.requests.append(self.path)
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        pass


class StallingHandler(BaseHTTPRequestHandler):
    """Handler that never answers within any sane timeout."""

    def do_GET(self) -> None:
        time.sleep(STALL_SECONDS)

    def log_message(self, format: str, *args: object) -> None:
        pass


def _serve(server: ThreadingHTTPServer) -> Iterator[str]:
    # serve_forever's default half-second poll would dominate the suite's runtime
    loop = functools.partial(server.serve_forever, poll_interval=0.01)
    thread = threading.Thread(target=loop, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def write_page(path: Path, match: str | None) -> None:
    """Write an index.html, with or without a baked-in switcher identity."""
    path.mkdir(parents=True, exist_ok=True)
    body = (
        "<html><body>docs</body></html>\n"
        if match is None
        else PAGE.format(match=match)
    )
    (path / "index.html").write_text(body, encoding="utf-8")


@pytest.fixture
def www(tmp_path: Path) -> Path:
    """Document root of the local test server."""
    root = tmp_path / "www"
    root.mkdir()
    return root


@pytest.fixture
def base_url(www: Path) -> Iterator[str]:
    """Serve :func:`www` over localhost for the duration of a test."""
    QuietHandler.requests.clear()
    handler = functools.partial(QuietHandler, directory=str(www))
    yield from _serve(ThreadingHTTPServer(("127.0.0.1", 0), handler))


@pytest.fixture
def stalling_url() -> Iterator[str]:
    """A server that accepts connections and then never replies."""
    yield from _serve(ThreadingHTTPServer(("127.0.0.1", 0), StallingHandler))
