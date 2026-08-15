"""Deploy and manage versioned Sphinx/static-HTML documentation sites."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from docstacks.deploy import DeployError, deploy, promote
from docstacks.lifecycle import PruneResult, delete, prune, retitle
from docstacks.manifest import Entry, Manifest
from docstacks.tree import scan_tree

try:
    __version__ = _version("docstacks")
except PackageNotFoundError:  # pragma: no cover - source checkout without install
    __version__ = "0.0.0+unknown"

__all__ = [
    "DeployError",
    "Entry",
    "Manifest",
    "PruneResult",
    "__version__",
    "delete",
    "deploy",
    "promote",
    "prune",
    "retitle",
    "scan_tree",
]
