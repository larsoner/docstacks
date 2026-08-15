"""Tests for :mod:`docstacks.tree`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from docstacks.tree import scan_tree, version_key


def test_version_key_orders_numerically() -> None:
    """Components sort as integers, and ``.x`` tops out its series."""
    names = ["1.9", "0.25.x", "1.12", "1.11.3", "1.11", "0.25.1"]
    assert sorted(names, key=version_key, reverse=True) == [
        "1.12",
        "1.11.3",
        "1.11",
        "1.9",
        "0.25.x",
        "0.25.1",
    ]


def test_scan_tree(site: Path) -> None:
    """Version dirs are found and ordered, junk is ignored, aliases resolve."""
    manifest = scan_tree(site, "https://mne.tools")
    assert [entry.version for entry in manifest] == [
        "dev",
        "1.12",
        "1.11",
        "1.9",
        "0.25.x",
    ]
    assert [entry.url for entry in manifest] == [
        "https://mne.tools/dev/",
        "https://mne.tools/stable/",
        "https://mne.tools/1.11/",
        "https://mne.tools/1.9/",
        "https://mne.tools/0.25.x/",
    ]
    assert [entry.display_name for entry in manifest] == [
        "dev",
        "1.12 (stable)",
        "1.11",
        "1.9",
        "0.25.x",
    ]
    assert [entry.version for entry in manifest if entry.preferred] == ["1.12"]
    assert manifest.validate() == []
    assert scan_tree(site, "https://mne.tools/") == manifest


def test_scan_tree_non_stable_alias(site: Path) -> None:
    """A non-``stable`` alias supplies the URL but not the preferred flag."""
    os.symlink("1.11", site / "maint", target_is_directory=True)
    entry = scan_tree(site, "https://mne.tools/").get("1.11")
    assert entry is not None
    assert entry.url == "https://mne.tools/maint/"
    assert entry.name == "1.11 (maint)"
    assert not entry.preferred


def test_scan_tree_ignores_unusable_symlinks(site: Path, tmp_path: Path) -> None:
    """Dangling, escaping, and non-version symlinks do not create entries."""
    os.symlink("9.9", site / "broken", target_is_directory=True)
    os.symlink("_images", site / "pics", target_is_directory=True)
    os.symlink(str(tmp_path), site / "escape", target_is_directory=True)
    manifest = scan_tree(site, "https://mne.tools/")
    assert [entry.version for entry in manifest] == [
        "dev",
        "1.12",
        "1.11",
        "1.9",
        "0.25.x",
    ]


def test_scan_tree_alias_chain(tmp_path: Path) -> None:
    """pandas-style ``stable -> 2.1 -> 2.1.3`` resolves to the real directory."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "2.1.3").mkdir()
    os.symlink("2.1.3", site / "2.1", target_is_directory=True)
    os.symlink("2.1", site / "stable", target_is_directory=True)
    manifest = scan_tree(site, "https://pandas.pydata.org/docs/")
    assert [entry.version for entry in manifest] == ["2.1.3"]
    entry = manifest.entries[0]
    assert entry.url == "https://pandas.pydata.org/docs/stable/"
    assert entry.name == "2.1.3 (stable)"
    assert entry.preferred


def test_scan_tree_custom_dev_versions(site: Path) -> None:
    """``dev_versions`` names are listed first, in the order given."""
    (site / "main").mkdir()
    manifest = scan_tree(site, "https://mne.tools/", dev_versions=("main", "dev"))
    assert [entry.version for entry in manifest][:2] == ["main", "dev"]
    assert scan_tree(site, "https://mne.tools/", dev_versions=()).get("dev") is None


def test_scan_tree_absolute_symlink(tmp_path: Path) -> None:
    """An absolute symlink to a sibling is still recognized as an alias."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "1.12").mkdir()
    os.symlink(str(site / "1.12"), site / "stable", target_is_directory=True)
    entry = scan_tree(site, "https://x/").get("1.12")
    assert entry is not None
    assert entry.url == "https://x/stable/"
    assert entry.preferred


def test_scan_tree_missing_dir(tmp_path: Path) -> None:
    """Scanning a directory that is not there is an error, not an empty result."""
    with pytest.raises(FileNotFoundError):
        scan_tree(tmp_path / "nope", "https://x/")
