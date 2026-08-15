"""Tests for :mod:`docstacks.manifest`."""

from __future__ import annotations

from pathlib import Path

import pytest

from docstacks.manifest import Entry, Manifest


def test_round_trip_is_byte_stable(mne_manifest_text: str, tmp_path: Path) -> None:
    """Order, foreign entries, and unknown keys survive a round-trip."""
    manifest = Manifest.loads(mne_manifest_text)
    assert [entry.version for entry in manifest] == ["dev", "1.12", "1.11", "legacy"]
    assert manifest.entries[2].extra == {"internal": "keep me"}
    assert manifest.entries[3].name == "≤ 0.20 (legacy)"
    assert manifest.dumps() == mne_manifest_text

    path = tmp_path / "out.json"
    manifest.dump(path)
    assert path.read_text(encoding="utf-8") == mne_manifest_text
    assert Manifest.load(path) == manifest


def test_round_trip_preserves_indent(
    mne_manifest_4space_text: str, tmp_path: Path
) -> None:
    """Reading a 4-space manifest and writing it back does not reindent it."""
    path = tmp_path / "versions.json"
    path.write_text(mne_manifest_4space_text, encoding="utf-8")
    manifest = Manifest.load(path)
    assert manifest.indent == 4
    manifest.set_preferred("1.11").set_preferred("1.12")
    manifest.dump(path)
    assert path.read_text(encoding="utf-8") == mne_manifest_4space_text


@pytest.mark.parametrize(
    ("text", "indent"),
    [("[]\n", 2), ("[\n\t{}\n]\n", "\t"), ('[\n   {"a": 1}\n]\n', 3)],
)
def test_indent_sniffing(text: str, indent: int | str) -> None:
    """Indent is taken from the first indented line, defaulting to two spaces."""
    assert Manifest.loads(text).indent == indent
    assert Manifest().indent == 2


def test_container_protocol(mne_manifest_text: str) -> None:
    """A manifest is sized, iterable, comparable, and legible in a traceback."""
    manifest = Manifest.loads(mne_manifest_text)
    assert len(manifest) == 4
    assert repr(manifest) == "<Manifest ['dev', '1.12', '1.11', 'legacy']>"
    assert manifest == Manifest.loads(mne_manifest_text)
    assert manifest != Manifest()
    assert manifest != manifest.entries


def test_serialization_omits_defaults() -> None:
    """``name`` and a false ``preferred`` are left out, extras come last."""
    entry = Entry(version="1.11", url="https://x/1.11/", extra={"z": 1, "a": 2})
    assert list(entry.to_dict()) == ["version", "url", "z", "a"]
    entry.name = "1.11"
    entry.preferred = True
    assert list(entry.to_dict()) == ["name", "version", "url", "preferred", "z", "a"]


def test_display_name() -> None:
    """``display_name`` falls back to the version."""
    assert Entry(version="dev", url="https://x/dev/").display_name == "dev"
    assert (
        Entry(version="dev", url="https://x/dev/", name="1.13").display_name == "1.13"
    )


@pytest.mark.parametrize(
    "text",
    ['{"version": "1.12"}', "[1]", '["1.12"]'],
)
def test_loads_rejects_bad_shapes(text: str) -> None:
    """Non-array documents and non-object entries are errors."""
    with pytest.raises(ValueError, match="must be a JSON (array|object)"):
        Manifest.loads(text)


def test_mutations(mne_manifest_text: str) -> None:
    """Mutators chain, insert at a position, and raise on unknown versions."""
    manifest = Manifest.loads(mne_manifest_text)
    manifest.add("1.13", "https://mne.tools/1.13/", "1.13", position=1).retitle(
        "1.11", "1.11 (old)"
    )
    assert [entry.version for entry in manifest][:3] == ["dev", "1.13", "1.12"]
    renamed = manifest.get("1.11")
    assert renamed is not None
    assert renamed.name == "1.11 (old)"

    manifest.remove("1.13")
    assert manifest.get("1.13") is None

    with pytest.raises(ValueError, match="already in the manifest"):
        manifest.add("1.12", "https://mne.tools/1.12/")
    for call in (manifest.remove, manifest.set_preferred):
        with pytest.raises(KeyError, match="not in the manifest"):
            call("9.9")
    with pytest.raises(KeyError, match="not in the manifest"):
        manifest.retitle("9.9", "nope")


def test_set_preferred_clears_previous(mne_manifest_text: str) -> None:
    """Setting preferred moves the flag rather than adding a second one."""
    manifest = Manifest.loads(mne_manifest_text)
    assert [entry.version for entry in manifest if entry.preferred] == ["1.12"]
    manifest.set_preferred("1.11")
    assert [entry.version for entry in manifest if entry.preferred] == ["1.11"]
    assert manifest.validate() == []


def test_validate_accepts_realistic_manifest(mne_manifest_text: str) -> None:
    """A real-world manifest, foreign entry and all, is valid."""
    assert Manifest.loads(mne_manifest_text).validate() == []
    assert Manifest().validate() == []


def test_validate_duplicate_version() -> None:
    """Repeated version identities are reported with their positions."""
    manifest = Manifest(
        [
            Entry(version="1.12", url="https://x/1.12/", preferred=True),
            Entry(version="1.12", url="https://x/stable/"),
        ]
    )
    assert manifest.validate() == ["duplicate version '1.12' (entries 0, 1)"]


def test_validate_preferred_count() -> None:
    """Zero and multiple preferred entries are both problems."""
    manifest = Manifest([Entry(version="1.12", url="https://x/1.12/")])
    assert manifest.validate() == ["no entry is marked preferred"]
    manifest.entries[0].preferred = True
    manifest.entries.append(
        Entry(version="1.11", url="https://x/1.11/", preferred=True)
    )
    assert manifest.validate() == ["multiple entries marked preferred: '1.12', '1.11'"]


def test_validate_missing_fields() -> None:
    """Empty version and url are reported, and an empty version is not a dupe."""
    manifest = Manifest(
        [
            Entry(version="", url="https://x/a/", preferred=True),
            Entry(version="", url=""),
        ]
    )
    assert manifest.validate() == [
        "entry 0: version is empty",
        "entry 1: version is empty",
        "entry 1: url is empty",
    ]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("ftp://x/1.12/", ["is not an http(s) URL"]),
        ("/1.12/", ["is not an http(s) URL"]),
        ("https://x/1.12", ["does not end with '/'"]),
        ("mailto:x", ["is not an http(s) URL", "does not end with '/'"]),
    ],
)
def test_validate_urls(url: str, expected: list[str]) -> None:
    """Non-http(s) URLs and URLs without a trailing slash are reported."""
    manifest = Manifest([Entry(version="1.12", url=url, preferred=True)])
    problems = manifest.validate()
    assert len(problems) == len(expected)
    for problem, fragment in zip(problems, expected, strict=True):
        assert problem.startswith("entry 0 ('1.12'): url ")
        assert fragment in problem
