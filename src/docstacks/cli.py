"""Command-line interface, kept thin over :mod:`docstacks.manifest`."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from docstacks import __version__
from docstacks.manifest import Manifest
from docstacks.tree import scan_tree

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docstacks",
        description="Manage versioned static documentation sites.",
    )
    parser.add_argument(
        "--version", action="version", version=f"docstacks {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser(
        "validate", help="check a versions.json for problems"
    )
    validate.add_argument("manifest", help="path to versions.json")
    validate.set_defaults(func=_run_validate)

    generate = subparsers.add_parser(
        "generate", help="build a versions.json from a deployed site directory"
    )
    generate.add_argument("site_dir", help="root of the deployed site")
    generate.add_argument(
        "--base-url", required=True, help="absolute URL the site is served from"
    )
    generate.add_argument(
        "--dev-name",
        action="append",
        dest="dev_names",
        metavar="NAME",
        help="directory name to treat as a development build (repeatable)",
    )
    generate.add_argument("-o", "--output", help="write to this file instead of stdout")
    generate.set_defaults(func=_run_generate)

    list_ = subparsers.add_parser("list", help="show the entries of a versions.json")
    list_.add_argument("manifest", help="path to versions.json")
    list_.set_defaults(func=_run_list)

    return parser


def _run_validate(args: argparse.Namespace) -> int:
    problems = Manifest.load(args.manifest).validate()
    for problem in problems:
        print(f"{args.manifest}: {problem}", file=sys.stderr)
    return 1 if problems else 0


def _run_generate(args: argparse.Namespace) -> int:
    manifest = scan_tree(
        args.site_dir, args.base_url, dev_versions=tuple(args.dev_names or ("dev",))
    )
    if args.output:
        manifest.dump(args.output)
    else:
        sys.stdout.write(manifest.dumps())
    return 0


def _run_list(args: argparse.Namespace) -> int:
    manifest = Manifest.load(args.manifest)
    rows = [
        (entry.version, entry.display_name, entry.url, "*" if entry.preferred else "")
        for entry in manifest
    ]
    headers = ("VERSION", "NAME", "URL", "")
    widths = [
        max(len(row[col]) for row in (*rows, headers)) for col in range(len(headers))
    ]
    for row in (headers, *rows):
        cells = zip(row, widths, strict=True)
        print(" ".join(value.ljust(width) for value, width in cells).rstrip())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``docstacks`` command-line interface.

    Parameters
    ----------
    argv : sequence of str | None
        Arguments to parse; :data:`sys.argv` is used when ``None``.

    Returns
    -------
    code : int
        Process exit status.
    """
    args = _build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
