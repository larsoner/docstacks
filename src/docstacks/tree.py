"""Derive a manifest from an already-deployed documentation tree.

A deployed site is one directory per version at the root, plus symlinks such as
``stable -> 1.12`` that alias a version under a stable URL. Alias chains are
common in the wild -- pandas ships ``stable -> 2.1 -> 2.1.3`` -- so symlinks are
resolved transitively and every link in the chain counts as an alias of the real
directory at the end. Anything else at the root (``CNAME``, ``.nojekyll``, a
landing ``index.html``, stray directories) is not ours and is ignored.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

from docstacks.manifest import Manifest

__all__ = ["scan_tree", "version_key"]

#: Directory names that look like a released version, e.g. ``1.12``, ``0.25.x``.
VERSION_RE = re.compile(r"^\d+\.\d+(\.\d+)?(\.x)?$")

#: Alias whose target is the canonical release.
PREFERRED_ALIAS = "stable"

_WILDCARD = 1 << 62


def version_key(version: str) -> tuple[int, ...]:
    """Sort key giving numeric ordering of dotted version strings.

    ``1.9`` sorts below ``1.12``, and a maintenance-branch component such as the
    ``x`` of ``0.25.x`` sorts above every concrete patch release of that series.

    Parameters
    ----------
    version : str
        Version string, e.g. ``"1.12"`` or ``"0.25.x"``.

    Returns
    -------
    key : tuple of int
        Tuple usable as a ``sorted`` key.
    """
    key: list[int] = []
    for part in version.split("."):
        key.append(int(part) if part.isdigit() else _WILDCARD)
    return tuple(key)


def scan_tree(
    site_dir: str | os.PathLike[str],
    base_url: str,
    dev_versions: Sequence[str] = ("dev",),
) -> Manifest:
    """Build a manifest describing a deployed documentation tree.

    Parameters
    ----------
    site_dir : path-like
        Root of the deployed site, i.e. the directory holding the per-version
        directories.
    base_url : str
        Absolute URL the site is served from. A trailing ``/`` is added if
        missing.
    dev_versions : sequence of str
        Directory names to treat as development builds. These are listed first,
        in the order given.

    Returns
    -------
    manifest : Manifest
        Development entries first, then numbered versions newest-first. A
        directory reached through alias symlinks is listed once, under the alias
        URL and named ``"<version> (<alias>)"``; the target of the ``stable``
        alias is marked preferred.
    """
    if not base_url.endswith("/"):
        base_url += "/"
    dev_versions = tuple(dev_versions)
    root = os.path.realpath(site_dir)

    versions: list[str] = []
    aliases: dict[str, list[str]] = {}
    with os.scandir(site_dir) as scan:
        for item in scan:
            if not item.is_dir():
                continue
            if item.is_symlink():
                target = _symlink_target(item.path, root)
                if target is not None:
                    aliases.setdefault(target, []).append(item.name)
            elif item.name in dev_versions or VERSION_RE.match(item.name):
                versions.append(item.name)

    known = set(versions)
    aliases = {
        target: sorted(names) for target, names in aliases.items() if target in known
    }

    ordered = [name for name in dev_versions if name in known]
    ordered += sorted(
        (name for name in versions if name not in dev_versions),
        key=version_key,
        reverse=True,
    )

    manifest = Manifest()
    for version in ordered:
        names = aliases.get(version, [])
        alias = _pick_alias(names)
        if alias is None:
            manifest.add(version, base_url + version + "/")
        else:
            manifest.add(
                version,
                base_url + alias + "/",
                name=f"{version} ({alias})",
                preferred=PREFERRED_ALIAS in names,
            )
    return manifest


def _pick_alias(names: list[str]) -> str | None:
    """Choose which of several aliases for one version supplies its URL."""
    if not names:
        return None
    return PREFERRED_ALIAS if PREFERRED_ALIAS in names else names[0]


def _aliases_pointing_at(site_dir: str | os.PathLike[str], version: str) -> list[str]:
    """Names of root symlinks whose chain ends at ``version``."""
    root = os.path.realpath(site_dir)
    with os.scandir(site_dir) as scan:
        return sorted(
            item.name
            for item in scan
            if item.is_symlink() and _symlink_target(item.path, root) == version
        )


def _symlink_target(path: str, root: str) -> str | None:
    """Name of the real directory a symlink chain ends at, if it stays in ``root``."""
    parent, name = os.path.split(os.path.realpath(path))
    if parent != root or not name:
        return None
    return name
