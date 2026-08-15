"""Model, (de)serialization, and validation of a ``versions.json`` manifest.

The schema is the one consumed by the pydata-sphinx-theme version switcher: a
JSON array of objects with ``version``, ``url``, and optional ``name`` and
``preferred`` keys. Entries that ``docstacks`` does not understand, and unknown
keys on entries it does, are carried through untouched so that hand-maintained
manifests survive a round-trip.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Entry", "Manifest"]

_KNOWN_KEYS = ("name", "version", "url", "preferred")


@dataclass
class Entry:
    """A single entry of a version switcher manifest.

    Parameters
    ----------
    version : str
        Version identity, matched by strict string equality against the
        ``version_match`` value baked into each documentation build. It need not
        be a version number; ``dev`` and ``legacy`` are legitimate values.
    url : str
        Absolute URL of the version's documentation root, ending in ``/``.
    name : str | None
        Display label. The theme falls back to ``version`` when absent.
    preferred : bool
        Whether this is the canonical release. Exactly one entry of a manifest
        should set it; the theme keys its "you are viewing an old version"
        banner off it.
    extra : dict
        Keys present in the source JSON that are not part of the schema above,
        in their original order.
    """

    version: str
    url: str
    name: str | None = None
    preferred: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        """Label to show in the switcher, falling back to :attr:`version`."""
        return self.version if self.name is None else self.name

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Entry:
        """Build an entry from a decoded JSON object.

        Parameters
        ----------
        data : dict
            Decoded JSON object.

        Returns
        -------
        entry : Entry
            The entry.
        """
        name = data.get("name")
        return cls(
            version=str(data.get("version", "")),
            url=str(data.get("url", "")),
            name=None if name is None else str(name),
            preferred=bool(data.get("preferred", False)),
            extra={k: v for k, v in data.items() if k not in _KNOWN_KEYS},
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable object.

        Returns
        -------
        data : dict
            Mapping with ``name``, ``version``, ``url``, ``preferred``, then any
            extra keys. ``name`` is omitted when ``None`` and ``preferred`` when
            false, matching how these files are written by hand.
        """
        data: dict[str, Any] = {}
        if self.name is not None:
            data["name"] = self.name
        data["version"] = self.version
        data["url"] = self.url
        if self.preferred:
            data["preferred"] = True
        data.update(self.extra)
        return data


class Manifest:
    """An ordered collection of :class:`Entry` objects.

    Parameters
    ----------
    entries : iterable of Entry | None
        Initial entries, in switcher order.
    """

    def __init__(self, entries: list[Entry] | None = None) -> None:
        self.entries: list[Entry] = list(entries or [])

    def __repr__(self) -> str:
        versions = ", ".join(repr(entry.version) for entry in self.entries)
        return f"<Manifest [{versions}]>"

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[Entry]:
        return iter(self.entries)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Manifest):
            return NotImplemented
        return self.entries == other.entries

    # -- serialization -----------------------------------------------------

    @classmethod
    def loads(cls, text: str) -> Manifest:
        """Parse a manifest from JSON text.

        Parameters
        ----------
        text : str
            JSON array of switcher entries.

        Returns
        -------
        manifest : Manifest
            The parsed manifest.
        """
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError(
                f"manifest must be a JSON array, got {type(data).__name__}"
            )
        entries = []
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                raise ValueError(
                    f"manifest entry {index} must be a JSON object, got "
                    f"{type(item).__name__}"
                )
            entries.append(Entry.from_dict(item))
        return cls(entries)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Manifest:
        """Read a manifest from a file.

        Parameters
        ----------
        path : path-like
            Path to a ``versions.json`` file.

        Returns
        -------
        manifest : Manifest
            The parsed manifest.
        """
        with open(path, encoding="utf-8") as fid:
            return cls.loads(fid.read())

    def dumps(self) -> str:
        """Serialize to JSON text.

        Returns
        -------
        text : str
            Two-space-indented JSON array with a trailing newline, non-ASCII
            characters left as-is.
        """
        data = [entry.to_dict() for entry in self.entries]
        return json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    def dump(self, path: str | os.PathLike[str]) -> None:
        """Write the manifest to a file.

        Parameters
        ----------
        path : path-like
            Destination path.
        """
        with open(path, "w", encoding="utf-8", newline="\n") as fid:
            fid.write(self.dumps())

    # -- lookup and mutation -----------------------------------------------

    def get(self, version: str) -> Entry | None:
        """Return the entry for ``version``, or ``None`` if there is none.

        Parameters
        ----------
        version : str
            Version identity to look up.

        Returns
        -------
        entry : Entry | None
            The first matching entry.
        """
        for entry in self.entries:
            if entry.version == version:
                return entry
        return None

    def add(
        self,
        version: str,
        url: str,
        name: str | None = None,
        *,
        preferred: bool = False,
        position: int | None = None,
    ) -> Manifest:
        """Add an entry.

        Parameters
        ----------
        version : str
            Version identity. Must not already be present.
        url : str
            Absolute URL of the version's documentation root.
        name : str | None
            Display label.
        preferred : bool
            Whether to mark this entry as the canonical release. Unlike
            :meth:`set_preferred`, this does not clear the flag elsewhere.
        position : int | None
            Index to insert at; appended at the end when ``None``.

        Returns
        -------
        manifest : Manifest
            The manifest, for chaining.
        """
        if self.get(version) is not None:
            raise ValueError(f"version {version!r} is already in the manifest")
        entry = Entry(version=version, url=url, name=name, preferred=preferred)
        if position is None:
            self.entries.append(entry)
        else:
            self.entries.insert(position, entry)
        return self

    def remove(self, version: str) -> Manifest:
        """Remove the entry for ``version``.

        Parameters
        ----------
        version : str
            Version identity to remove.

        Returns
        -------
        manifest : Manifest
            The manifest, for chaining.
        """
        entry = self.get(version)
        if entry is None:
            raise KeyError(f"version {version!r} is not in the manifest")
        self.entries.remove(entry)
        return self

    def retitle(self, version: str, name: str | None) -> Manifest:
        """Change the display label of an entry.

        Parameters
        ----------
        version : str
            Version identity to relabel.
        name : str | None
            New display label, or ``None`` to fall back to the version.

        Returns
        -------
        manifest : Manifest
            The manifest, for chaining.
        """
        entry = self.get(version)
        if entry is None:
            raise KeyError(f"version {version!r} is not in the manifest")
        entry.name = name
        return self

    def set_preferred(self, version: str) -> Manifest:
        """Mark one entry preferred, clearing the flag on all others.

        Parameters
        ----------
        version : str
            Version identity to mark.

        Returns
        -------
        manifest : Manifest
            The manifest, for chaining.
        """
        if self.get(version) is None:
            raise KeyError(f"version {version!r} is not in the manifest")
        for entry in self.entries:
            entry.preferred = entry.version == version
        return self

    # -- validation --------------------------------------------------------

    def validate(self) -> list[str]:
        """Check the manifest for problems.

        Returns
        -------
        problems : list of str
            Human-readable descriptions of everything wrong with the manifest.
            An empty list means it is valid.
        """
        problems: list[str] = []
        seen: dict[str, list[int]] = {}
        for index, entry in enumerate(self.entries):
            label = _label(index, entry)
            if not entry.version:
                problems.append(f"{label}: version is empty")
            else:
                seen.setdefault(entry.version, []).append(index)
            if not entry.url:
                problems.append(f"{label}: url is empty")
            else:
                if not entry.url.lower().startswith(("http://", "https://")):
                    problems.append(f"{label}: url {entry.url!r} is not an http(s) URL")
                if not entry.url.endswith("/"):
                    problems.append(f"{label}: url {entry.url!r} does not end with '/'")
        for version, indices in seen.items():
            if len(indices) > 1:
                where = ", ".join(str(index) for index in indices)
                problems.append(f"duplicate version {version!r} (entries {where})")
        preferred = [entry.version for entry in self.entries if entry.preferred]
        if len(preferred) > 1:
            where = ", ".join(repr(version) for version in preferred)
            problems.append(f"multiple entries marked preferred: {where}")
        elif not preferred and self.entries:
            problems.append("no entry is marked preferred")
        return problems


def _label(index: int, entry: Entry) -> str:
    if entry.version:
        return f"entry {index} ({entry.version!r})"
    return f"entry {index}"
