"""Tests for :mod:`docstacks.cli`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docstacks.cli import main
from docstacks.manifest import Manifest


def test_validate_ok(mne_manifest_path: Path, capsys: pytest.CaptureFixture) -> None:
    """A valid manifest exits 0 and says nothing."""
    assert main(["validate", str(mne_manifest_path)]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_validate_problems(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """Problems go to stderr and exit 1."""
    path = tmp_path / "versions.json"
    path.write_text('[{"version": "1.12", "url": "https://x/1.12"}]', encoding="utf-8")
    assert main(["validate", str(path)]) == 1
    err = capsys.readouterr().err
    assert "does not end with '/'" in err
    assert "no entry is marked preferred" in err


def test_generate_stdout(site: Path, capsys: pytest.CaptureFixture) -> None:
    """``generate`` writes a manifest to stdout by default."""
    assert main(["generate", str(site), "--base-url", "https://mne.tools/"]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    manifest = Manifest.loads(captured.out)
    assert [entry.version for entry in manifest][:2] == ["dev", "1.12"]
    assert manifest.validate() == []


def test_generate_output_file(site: Path, tmp_path: Path) -> None:
    """``-o`` writes to a file, and ``--dev-name`` overrides the dev dirs."""
    (site / "main").mkdir()
    out = tmp_path / "versions.json"
    assert (
        main(
            [
                "generate",
                str(site),
                "--base-url",
                "https://mne.tools/",
                "--dev-name",
                "main",
                "-o",
                str(out),
            ]
        )
        == 0
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0]["version"] == "main"
    assert "dev" not in [entry["version"] for entry in data]


def test_generate_warns_without_stable(
    site: Path, capsys: pytest.CaptureFixture
) -> None:
    """A site with no ``stable`` symlink still succeeds, but says so on stderr."""
    (site / "stable").unlink()
    assert main(["generate", str(site), "--base-url", "https://mne.tools/"]) == 0
    captured = capsys.readouterr()
    assert "warning: no 'stable' symlink" in captured.err
    assert Manifest.loads(captured.out).validate() == ["no entry is marked preferred"]


def test_list(mne_manifest_path: Path, capsys: pytest.CaptureFixture) -> None:
    """``list`` prints an aligned table with a marker on the preferred entry."""
    assert main(["list", str(mne_manifest_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["VERSION", "NAME", "URL"]
    assert lines[1].startswith("dev")
    assert lines[2].endswith("*")
    assert [line.split()[0] for line in lines[1:]] == ["dev", "1.12", "1.11", "legacy"]
    assert len({line.index("https://") for line in lines[1:]}) == 1


def test_version_flag(capsys: pytest.CaptureFixture) -> None:
    """``--version`` prints and exits 0."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0
    assert capsys.readouterr().out.startswith("docstacks ")


def test_no_command() -> None:
    """A missing subcommand is a usage error."""
    with pytest.raises(SystemExit) as excinfo:
        main([])
    assert excinfo.value.code == 2
