"""Tests for :mod:`docstacks.cli`."""

from __future__ import annotations

import json
import os
import runpy
import sys
from pathlib import Path

import pytest

from conftest import add_commits, git, write_page
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


def test_validate_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """A manifest that is not there exits 1 with a message, not a traceback."""
    assert main(["validate", str(tmp_path / "absent.json")]) == 1
    assert "error: " in capsys.readouterr().err


def test_validate_live(
    www: Path, base_url: str, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The switcher checks are opt-in, and catch what the schema cannot."""
    write_page(www / "stable", "1.12")
    write_page(www / "1.11", "1.11")
    path = tmp_path / "versions.json"
    manifest = Manifest()
    manifest.add("stable", f"{base_url}stable/", preferred=True)
    manifest.add("1.11", f"{base_url}1.11/")
    manifest.dump(path)

    assert main(["validate", str(path)]) == 0
    assert capsys.readouterr().err == ""

    assert main(["validate", str(path), "--check-urls", "--check-match"]) == 1
    err = capsys.readouterr().err
    assert err.startswith(f"{path}: ")
    assert "version mismatch" in err
    assert "'1.12'" in err and "'stable'" in err


def test_validate_ignore(
    www: Path, base_url: str, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """``--ignore`` lets a site with permanently broken archives reach exit 0."""
    write_page(www / "1.12", "1.12")
    path = tmp_path / "versions.json"
    manifest = Manifest()
    manifest.add("1.12", f"{base_url}1.12/", preferred=True)
    manifest.add("0.21", f"{base_url}0.21/")
    manifest.add("0.20", f"{base_url}0.20/")
    manifest.dump(path)

    argv = ["validate", str(path), "--check-urls", "--check-match"]
    assert main(argv) == 1
    assert capsys.readouterr().err.count("\n") == 2

    assert main([*argv, "--ignore", "0.21", "--ignore", "0.20"]) == 0
    assert capsys.readouterr().err == ""


def test_validate_remote_manifest(www: Path, base_url: str) -> None:
    """The manifest itself can be fetched from where it is served."""
    Manifest().add("1.12", "https://mne.tools/1.12/", preferred=True).dump(
        www / "versions.json"
    )
    assert main(["validate", f"{base_url}versions.json"]) == 0


def test_validate_remote_timeout(
    stalling_url: str, capsys: pytest.CaptureFixture
) -> None:
    """``--timeout`` reaches the fetch rather than being ignored."""
    argv = ["validate", f"{stalling_url}versions.json", "--timeout", "0.25"]
    assert main(argv) == 1
    assert "cannot fetch" in capsys.readouterr().err


def test_validate_site_dir(
    site: Path, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """``--site-dir`` cross-checks offline, honouring ``--dev-name``."""
    path = tmp_path / "versions.json"
    Manifest().add("1.12", "https://x/1.12/", preferred=True).dump(path)

    assert main(["validate", str(path), "--site-dir", str(site)]) == 1
    err = capsys.readouterr().err
    assert "'dev' is deployed" in err
    assert "'1.11' is deployed" in err

    argv = ["validate", str(path), "--site-dir", str(site), "--dev-name", "main"]
    assert main(argv) == 1
    assert "'dev' is deployed" not in capsys.readouterr().err


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


def test_deploy(
    html_dir: Path,
    site_repo: Path,
    bare_remote: Path,
    capsys: pytest.CaptureFixture,
) -> None:
    """The happy path deploys, aliases, pushes, and reports the short SHA."""
    assert (
        main(
            [
                "deploy",
                str(html_dir),
                "1.12",
                "--repo",
                str(site_repo),
                "--alias",
                "stable",
                "--base-url",
                "https://mne.tools/",
                "--source-sha",
                "cafe1234",
                "--push",
            ]
        )
        == 0
    )
    sha = git(site_repo, "rev-parse", "HEAD")
    out = capsys.readouterr().out
    assert out.startswith(sha[:8])
    assert "deployed 1.12" in out
    assert "aliases: stable" in out
    assert "pushed to origin" in out
    assert os.readlink(site_repo / "stable") == "1.12"
    assert git(bare_remote, "rev-parse", "main") == sha


def test_deploy_no_manifest_in_cwd(
    html_dir: Path, site_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``--repo`` defaults to the working directory, and ``--no-manifest`` skips it."""
    before = (site_repo / "versions.json").read_bytes()
    monkeypatch.chdir(site_repo)
    assert main(["deploy", str(html_dir), "1.12", "--no-manifest"]) == 0
    assert (site_repo / "versions.json").read_bytes() == before
    assert (site_repo / "1.12" / "index.html").is_file()


def test_deploy_failure(
    html_dir: Path, site_repo: Path, capsys: pytest.CaptureFixture
) -> None:
    """A refused deploy exits nonzero with the reason on stderr."""
    (site_repo / "CNAME").write_text("other.example\n", encoding="utf-8")
    assert main(["deploy", str(html_dir), "1.12", "--repo", str(site_repo)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error: " in captured.err
    assert "uncommitted changes" in captured.err


def test_promote(
    html_dir: Path, release_repo: Path, capsys: pytest.CaptureFixture
) -> None:
    """``promote`` defaults to the stable alias and demotes the incumbent."""
    assert (
        main(
            [
                "promote",
                str(html_dir),
                "1.13",
                "--repo",
                str(release_repo),
                "--base-url",
                "https://mne.tools/",
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    assert "promoted 1.13" in out
    assert "aliases: stable" in out
    assert os.readlink(release_repo / "stable") == "1.13"
    manifest = Manifest.load(release_repo / "versions.json")
    demoted = manifest.get("1.12")
    assert demoted is not None
    assert (demoted.name, demoted.url) == (None, "https://mne.tools/1.12/")


def test_retitle_and_delete(release_repo: Path, capsys: pytest.CaptureFixture) -> None:
    """The manifest-only lifecycle commands report the commit they made."""
    assert (
        main(["retitle", "1.11", "1.11 (archived)", "--repo", str(release_repo)]) == 0
    )
    assert "named '1.11 (archived)'" in capsys.readouterr().out

    assert main(["retitle", "1.11", "", "--repo", str(release_repo)]) == 0
    assert "cleared the name of 1.11" in capsys.readouterr().out

    assert main(["delete", "1.11", "--repo", str(release_repo)]) == 0
    assert "deleted 1.11" in capsys.readouterr().out
    assert not (release_repo / "1.11").exists()

    assert main(["delete", "1.12", "--repo", str(release_repo)]) == 1
    assert "is the preferred version" in capsys.readouterr().err


def test_prune(site_repo: Path, capsys: pytest.CaptureFixture) -> None:
    """A rewrite is summarized on stdout and warned about on stderr."""
    add_commits(site_repo, 5)
    assert main(["prune", "--repo", str(site_repo), "--keep", "2"]) == 0

    captured = capsys.readouterr()
    assert "4 commits squashed into a new root, 2 preserved" in captured.out
    assert "WARNING: history was rewritten" in captured.err
    assert "git push --force-with-lease origin main" in captured.err
    assert int(git(site_repo, "rev-list", "--count", "HEAD")) == 3


def test_prune_push(
    site_repo: Path, bare_remote: Path, capsys: pytest.CaptureFixture
) -> None:
    """``--push`` force-pushes and says so instead of printing the command."""
    add_commits(site_repo, 5)
    assert (
        main(["prune", "--repo", str(site_repo), "--keep-since", "HEAD~1", "--push"])
        == 0
    )

    captured = capsys.readouterr()
    assert "force-pushed to origin" in captured.err
    assert "git push --force-with-lease" not in captured.err
    assert git(bare_remote, "rev-parse", "main") == git(site_repo, "rev-parse", "HEAD")


def test_prune_no_op(site_repo: Path, capsys: pytest.CaptureFixture) -> None:
    """Nothing to collapse is a quiet success."""
    assert main(["prune", "--repo", str(site_repo), "--keep", "1"]) == 0
    captured = capsys.readouterr()
    assert "nothing to prune" in captured.out
    assert captured.err == ""


def test_prune_requires_an_anchor(site_repo: Path) -> None:
    """``--keep`` and ``--keep-since`` are mutually exclusive and one is required."""
    for argv in ([], ["--keep", "2", "--keep-since", "HEAD"]):
        with pytest.raises(SystemExit) as excinfo:
            main(["prune", "--repo", str(site_repo), *argv])
        assert excinfo.value.code == 2


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


def test_python_m(
    mne_manifest_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
) -> None:
    monkeypatch.setattr(sys, "argv", ["docstacks", "list", str(mne_manifest_path)])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("docstacks", run_name="__main__")
    assert excinfo.value.code == 0
    assert "stable" in capsys.readouterr().out
