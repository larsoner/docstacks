"""Tests for :mod:`docstacks.check`."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from conftest import QuietHandler, write_page
from docstacks.check import CheckError, check_live, check_site_dir, load_manifest
from docstacks.manifest import Entry, Manifest

#: Enough of a timeout to fail fast against a server that never answers.
STALL_TIMEOUT = 0.25


def manifest_for(base_url: str, *versions: str) -> Manifest:
    """A manifest whose entries point at directories of the local test server."""
    manifest = Manifest()
    for version in versions:
        manifest.add(version, f"{base_url}{version}/")
    return manifest


# -- fetching the manifest itself ------------------------------------------


def test_load_manifest_local(mne_manifest_path: Path) -> None:
    """A path is still a path."""
    assert [entry.version for entry in load_manifest(str(mne_manifest_path))] == [
        "dev",
        "1.12",
        "1.11",
        "legacy",
    ]


def test_load_manifest_remote(www: Path, base_url: str, mne_manifest_text: str) -> None:
    """A live versions.json can be validated where it is actually served."""
    (www / "versions.json").write_text(mne_manifest_text, encoding="utf-8")
    manifest = load_manifest(f"{base_url}versions.json")
    assert [entry.version for entry in manifest] == ["dev", "1.12", "1.11", "legacy"]
    assert manifest.validate() == []


def test_load_manifest_remote_missing(base_url: str) -> None:
    """A non-200 is an error, not an empty manifest."""
    with pytest.raises(CheckError, match="returned HTTP 404"):
        load_manifest(f"{base_url}versions.json")


def test_load_manifest_remote_invalid(www: Path, base_url: str) -> None:
    """Whatever came back has to be a manifest."""
    (www / "versions.json").write_text('{"nope": 1}', encoding="utf-8")
    with pytest.raises(CheckError, match="is not a valid manifest"):
        load_manifest(f"{base_url}versions.json")


def test_load_manifest_unreachable(stalling_url: str) -> None:
    """A server that never answers is reported rather than hung on."""
    with pytest.raises(CheckError, match="cannot fetch"):
        load_manifest(f"{stalling_url}versions.json", timeout=STALL_TIMEOUT)


# -- --check-urls ----------------------------------------------------------


def test_check_urls(www: Path, base_url: str) -> None:
    """Every entry is fetched, and a broken one does not stop the rest."""
    write_page(www / "1.12", "1.12")
    write_page(www / "1.11", "1.11")
    manifest = manifest_for(base_url, "1.12", "0.9", "1.11")

    problems = check_live(manifest, urls=True)

    assert len(problems) == 1
    assert problems[0].startswith("entry 1 ('0.9'):")
    assert "returned HTTP 404" in problems[0]


def test_check_urls_unreachable(stalling_url: str) -> None:
    """A timeout is a problem for that entry, phrased as a transport failure."""
    manifest = manifest_for(stalling_url, "1.12")
    problems = check_live(manifest, urls=True, timeout=STALL_TIMEOUT)
    assert len(problems) == 1
    assert problems[0].startswith("entry 0 ('1.12'): cannot reach")


# -- --check-match ---------------------------------------------------------


def test_check_match(www: Path, base_url: str) -> None:
    """A page whose baked-in identity agrees with the manifest is fine."""
    write_page(www / "1.12", "1.12")
    assert check_live(manifest_for(base_url, "1.12"), match=True) == []


def test_check_match_mismatch(www: Path, base_url: str) -> None:
    """MNE's real bug: /stable/ is built as 1.12 but listed as 'stable'."""
    write_page(www / "stable", "1.12")
    manifest = manifest_for(base_url, "stable")

    problems = check_live(manifest, match=True)

    assert len(problems) == 1
    assert "version mismatch" in problems[0]
    assert "'1.12'" in problems[0]
    assert "'stable'" in problems[0]


def test_check_match_absent(www: Path, base_url: str) -> None:
    """A page with no switcher identity at all gets its own finding."""
    write_page(www / "1.12", None)
    problems = check_live(manifest_for(base_url, "1.12"), match=True)
    assert len(problems) == 1
    assert "no version_match found" in problems[0]


def test_check_match_skips_unreachable_pages(www: Path, base_url: str) -> None:
    """A 404 is reported once, not followed by a bogus match complaint."""
    problems = check_live(manifest_for(base_url, "0.9"), urls=True, match=True)
    assert len(problems) == 1
    assert "returned HTTP 404" in problems[0]


# -- request economy and opt-in --------------------------------------------


def test_check_live_fetches_each_entry_once(www: Path, base_url: str) -> None:
    """Both checks share one GET per entry."""
    write_page(www / "1.12", "1.12")
    check_live(manifest_for(base_url, "1.12"), urls=True, match=True)
    assert QuietHandler.requests == ["/1.12/"]


def test_check_live_is_opt_in(base_url: str) -> None:
    """With neither check asked for, the network is not touched."""
    assert check_live(manifest_for(base_url, "1.12")) == []
    assert QuietHandler.requests == []


def test_check_live_skips_entries_without_a_url(base_url: str) -> None:
    """An empty URL is Manifest.validate's problem, not the network layer's."""
    manifest = Manifest([Entry(version="1.12", url="")])
    assert check_live(manifest, urls=True) == []
    assert QuietHandler.requests == []


# -- --site-dir ------------------------------------------------------------


def test_check_site_dir_clean(site: Path) -> None:
    """A manifest that matches the tree exactly has nothing to report."""
    manifest = manifest_for("https://x/", "dev", "1.12", "1.11", "1.9", "0.25.x")
    assert check_site_dir(manifest, site) == []


def test_check_site_dir_missing_and_unlisted(site: Path) -> None:
    """Entries with no directory, and directories with no entry, both surface."""
    manifest = manifest_for("https://x/", "dev", "1.12", "0.9")

    problems = check_site_dir(manifest, site)

    assert problems[0].startswith("entry 2 ('0.9'): no directory or alias named '0.9'")
    assert [problem.split(" ")[0] for problem in problems[1:]] == [
        "'0.25.x'",
        "'1.11'",
        "'1.9'",
    ]
    assert all("has no manifest entry" in problem for problem in problems[1:])


def test_check_site_dir_ignores_foreign_entries(site: Path) -> None:
    """A legacy catch-all has no directory of its own and must not be flagged."""
    manifest = manifest_for("https://x/", "dev", "1.12", "1.11", "1.9", "0.25.x")
    manifest.add("legacy", "https://x/dev/old_versions/")
    assert check_site_dir(manifest, site) == []


def test_check_site_dir_accepts_an_alias(site: Path) -> None:
    """A version served through a symlink counts as deployed."""
    os.symlink("1.12", site / "2.1", target_is_directory=True)
    manifest = manifest_for("https://x/", "dev", "1.12", "1.11", "1.9", "0.25.x", "2.1")
    assert check_site_dir(manifest, site) == []


def test_check_site_dir_dev_names(site: Path) -> None:
    """Development directory names are configurable, like everywhere else."""
    manifest = manifest_for("https://x/", "1.12", "1.11", "1.9", "0.25.x")
    problems = check_site_dir(manifest, site, dev_versions=())
    assert problems == []
    problems = check_site_dir(manifest, site, dev_versions=("dev", "main"))
    assert problems == [
        "'dev' is deployed in " + str(site) + " but has no manifest entry"
    ]
