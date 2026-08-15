"""Checks that go beyond the manifest's own schema.

The pydata-sphinx-theme switcher matches an entry by comparing its ``version``
against the ``version_match`` value baked into each build's HTML, by **strict
string equality**. Nothing in the manifest can reveal a mismatch, because the
other half of the comparison lives in the deployed pages -- so MNE shipped a
manifest whose stable entry said ``"version": "stable"`` while the pages it
pointed at were built with ``version_match = '1.12'``, and the switcher silently
fell back to "Choose version" for every visitor. :func:`check_live` fetches the
pages and compares the two halves, which is the only way to catch that.

Everything here is opt-in: the network is not touched unless a caller asks.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from docstacks.manifest import Manifest, _label
from docstacks.tree import VERSION_RE

__all__ = ["CheckError", "check_live", "check_site_dir", "load_manifest"]

#: Seconds to wait for any single request.
DEFAULT_TIMEOUT = 10.0

USER_AGENT = "docstacks (+https://github.com/larsoner/docstacks)"

#: The assignment pydata-sphinx-theme writes into every page it builds.
VERSION_MATCH_RE = re.compile(
    r"""theme_switcher_version_match\s*=\s*['"]([^'"]*)['"]"""
)


class CheckError(RuntimeError):
    """A manifest could not be read from where it was asked for."""


@dataclass
class _Page:
    """Outcome of one HTTP GET."""

    status: int | None
    body: str
    reason: str


def load_manifest(source: str, *, timeout: float = DEFAULT_TIMEOUT) -> Manifest:
    """Read a manifest from a local path or an http(s) URL.

    Parameters
    ----------
    source : str
        Filesystem path, or an ``http://``/``https://`` URL of a live
        ``versions.json``.
    timeout : float
        Seconds to wait, when ``source`` is a URL.

    Returns
    -------
    manifest : Manifest
        The parsed manifest.
    """
    if not source.lower().startswith(("http://", "https://")):
        return Manifest.load(source)
    page = _get(source, timeout)
    if page.status is None:
        raise CheckError(f"cannot fetch {source} ({page.reason})")
    if not _is_ok(page.status):
        raise CheckError(f"{source} returned HTTP {page.status}")
    try:
        return Manifest.loads(page.body)
    except ValueError as exc:
        raise CheckError(f"{source} is not a valid manifest: {exc}") from exc


def check_live(
    manifest: Manifest,
    *,
    urls: bool = False,
    match: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    ignore: Collection[str] = (),
) -> list[str]:
    """Fetch the pages a manifest points at and report what is wrong with them.

    Each entry is fetched at most once, whichever checks are enabled, and one
    entry's failure never stops the others from being checked.

    Parameters
    ----------
    manifest : Manifest
        Manifest to check.
    urls : bool
        Report entries whose URL is unreachable or answers with a non-2xx
        status.
    match : bool
        Report entries whose page carries a ``version_match`` that differs from
        the entry's ``version``, or carries none at all.
    timeout : float
        Seconds to wait for each request.
    ignore : collection of str
        Versions to skip entirely -- they are never fetched, so a permanently
        broken archive is not hammered on every run. A version that matches no
        entry is accepted in silence: manifests change over time, and a cron
        job should not start failing the day an old entry is finally removed.

    Returns
    -------
    problems : list of str
        Human-readable descriptions, phrased so they are distinguishable from
        the schema problems :meth:`Manifest.validate` reports.
    """
    problems: list[str] = []
    if not (urls or match):
        return problems
    for index, entry in enumerate(manifest):
        if not entry.url or entry.version in ignore:
            continue
        label = _label(index, entry)
        page = _get(entry.url, timeout)
        if page.status is None:
            problems.append(f"{label}: cannot reach {entry.url} ({page.reason})")
            continue
        if not _is_ok(page.status):
            problems.append(f"{label}: {entry.url} returned HTTP {page.status}")
            continue
        if match:
            problems.extend(_check_match(label, entry.url, entry.version, page.body))
    return problems


def check_site_dir(
    manifest: Manifest,
    site_dir: str | os.PathLike[str],
    dev_versions: Sequence[str] = ("dev",),
    ignore: Collection[str] = (),
) -> list[str]:
    """Cross-check a manifest against a deployed tree, without any network.

    Entries whose version is neither version-shaped nor a development name are
    left alone: a hand-added catch-all such as MNE's ``legacy`` entry points
    into a subdirectory of another version and has no directory of its own.

    Parameters
    ----------
    manifest : Manifest
        Manifest to check.
    site_dir : path-like
        Root of the deployed site.
    dev_versions : sequence of str
        Directory names that count as development builds.
    ignore : collection of str
        Names to leave alone, in both directions: an ignored entry is not
        required to have a directory, and an ignored directory is not required
        to have an entry. Names matching nothing are accepted in silence.

    Returns
    -------
    problems : list of str
        Entries with nothing deployed, and deployed versions nothing lists.
    """
    site = Path(site_dir)
    present: set[str] = set()
    directories: set[str] = set()
    with os.scandir(site) as scan:
        for item in scan:
            if not item.is_dir():
                continue
            present.add(item.name)
            if not item.is_symlink():
                directories.add(item.name)

    problems: list[str] = []
    listed = {entry.version for entry in manifest}
    for index, entry in enumerate(manifest):
        if entry.version in ignore or not _is_version(entry.version, dev_versions):
            continue
        if entry.version not in present:
            problems.append(
                f"{_label(index, entry)}: no directory or alias named "
                f"{entry.version!r} in {site}"
            )
    for name in sorted(directories - listed - set(ignore)):
        if _is_version(name, dev_versions):
            problems.append(f"{name!r} is deployed in {site} but has no manifest entry")
    return problems


def _check_match(label: str, url: str, version: str, body: str) -> list[str]:
    """Compare the identity baked into a page with the one the manifest claims."""
    found = VERSION_MATCH_RE.search(body)
    if found is None:
        return [
            f"{label}: no version_match found in {url}; the switcher cannot "
            "match this entry (old theme, or not a pydata-sphinx-theme page)"
        ]
    if found.group(1) != version:
        return [
            f"{label}: version mismatch, {url} is built with version_match "
            f"{found.group(1)!r} but the manifest lists version {version!r}"
        ]
    return []


def _is_version(name: str, dev_versions: Sequence[str]) -> bool:
    return bool(VERSION_RE.match(name)) or name in dev_versions


def _is_ok(status: int) -> bool:
    return 200 <= status < 300


def _get(url: str, timeout: float) -> _Page:
    """GET ``url``, turning every transport failure into a describable outcome."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return _Page(status=int(response.status), body=body, reason="")
    except HTTPError as exc:
        return _Page(status=int(exc.code), body="", reason=str(exc.reason))
    except (OSError, HTTPException, ValueError) as exc:
        return _Page(status=None, body="", reason=str(getattr(exc, "reason", exc)))
