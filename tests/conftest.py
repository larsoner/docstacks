"""Shared fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

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


@pytest.fixture
def mne_manifest_text() -> str:
    """Realistic ``versions.json`` text."""
    return MNE_MANIFEST


@pytest.fixture
def mne_manifest_path(tmp_path: Path) -> Path:
    """Realistic ``versions.json`` on disk."""
    path = tmp_path / "versions.json"
    path.write_text(MNE_MANIFEST, encoding="utf-8")
    return path


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
