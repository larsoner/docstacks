"""Command-line interface, kept thin over the library modules."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from docstacks import __version__
from docstacks._git import GitError
from docstacks.check import (
    DEFAULT_TIMEOUT,
    CheckError,
    check_live,
    check_site_dir,
    load_manifest,
)
from docstacks.deploy import DEFAULT_MANIFEST, DeployError, deploy, promote
from docstacks.lifecycle import delete, prune, retitle
from docstacks.manifest import Manifest
from docstacks.tree import PREFERRED_ALIAS, scan_tree

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
    validate.add_argument("manifest", help="path or http(s) URL of a versions.json")
    validate.add_argument(
        "--check-urls",
        action="store_true",
        help="fetch every entry's URL and report unreachable or non-2xx pages",
    )
    validate.add_argument(
        "--check-match",
        action="store_true",
        help="fetch every entry's page and compare its baked-in version_match "
        "with the entry's version",
    )
    validate.add_argument(
        "--site-dir", help="deployed site to cross-check the manifest against"
    )
    validate.add_argument(
        "--ignore",
        action="append",
        dest="ignore",
        metavar="VERSION",
        help="version to exempt from the deployed-world checks, for archives "
        "that will never pass (repeatable)",
    )
    validate.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        metavar="SECONDS",
        help=f"seconds to wait for each request (default: {DEFAULT_TIMEOUT:g})",
    )
    _add_dev_name_argument(validate)
    validate.set_defaults(func=_run_validate)

    generate = subparsers.add_parser(
        "generate", help="build a versions.json from a deployed site directory"
    )
    generate.add_argument("site_dir", help="root of the deployed site")
    generate.add_argument(
        "--base-url", required=True, help="absolute URL the site is served from"
    )
    _add_dev_name_argument(generate)
    generate.add_argument("-o", "--output", help="write to this file instead of stdout")
    generate.set_defaults(func=_run_generate)

    list_ = subparsers.add_parser("list", help="show the entries of a versions.json")
    list_.add_argument("manifest", help="path to versions.json")
    list_.set_defaults(func=_run_list)

    deploy_ = subparsers.add_parser(
        "deploy", help="deploy a built HTML tree into a site repository"
    )
    _add_deploy_arguments(deploy_)
    deploy_.add_argument(
        "--no-manifest",
        action="store_true",
        help="deploy content without touching versions.json",
    )
    deploy_.set_defaults(func=_run_deploy)

    promote_ = subparsers.add_parser(
        "promote", help="deploy a version, alias it, and demote its predecessor"
    )
    _add_deploy_arguments(promote_)
    promote_.set_defaults(func=_run_promote, no_manifest=False)

    retitle_ = subparsers.add_parser(
        "retitle", help="change the display label of a manifest entry"
    )
    retitle_.add_argument("version", help="version identity to relabel")
    retitle_.add_argument("name", help="new label; pass '' to clear it")
    _add_repo_arguments(retitle_)
    retitle_.set_defaults(func=_run_retitle)

    delete_ = subparsers.add_parser(
        "delete", help="remove a version directory and its manifest entry"
    )
    delete_.add_argument("version", help="version identity to remove")
    _add_repo_arguments(delete_)
    delete_.set_defaults(func=_run_delete)

    prune_ = subparsers.add_parser(
        "prune", help="collapse history older than an anchor into one commit"
    )
    anchor = prune_.add_mutually_exclusive_group(required=True)
    anchor.add_argument(
        "--keep", type=int, metavar="N", help="number of commits to preserve"
    )
    anchor.add_argument(
        "--keep-since", metavar="REV", help="oldest revision to preserve"
    )
    _add_repo_arguments(prune_)
    prune_.set_defaults(func=_run_prune)

    return parser


def _add_dev_name_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dev-name",
        action="append",
        dest="dev_names",
        metavar="NAME",
        help="directory name to treat as a development build (repeatable)",
    )


def _add_repo_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo", default=".", help="checkout of the site repository (default: .)"
    )
    parser.add_argument("--push", action="store_true", help="push to origin afterwards")


def _add_deploy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("html_dir", help="directory of already-built HTML")
    parser.add_argument("version", help="version identity and directory name")
    _add_repo_arguments(parser)
    parser.add_argument(
        "--alias",
        action="append",
        dest="aliases",
        metavar="NAME",
        help="symlink NAME at the site root to this version (repeatable)",
    )
    parser.add_argument("--base-url", help="absolute URL the site is served from")
    parser.add_argument("--name", help="display label for the manifest entry")
    parser.add_argument(
        "--source-sha", help="revision that produced the HTML, recorded as a trailer"
    )
    parser.add_argument("--message", help="commit subject")


def _run_validate(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.manifest, timeout=args.timeout)
    ignore = frozenset(args.ignore or ())
    problems = manifest.validate()
    if args.site_dir:
        problems += check_site_dir(
            manifest, args.site_dir, dev_versions=_dev_versions(args), ignore=ignore
        )
    problems += check_live(
        manifest,
        ignore=ignore,
        urls=args.check_urls,
        match=args.check_match,
        timeout=args.timeout,
    )
    for problem in problems:
        print(f"{args.manifest}: {problem}", file=sys.stderr)
    return 1 if problems else 0


def _dev_versions(args: argparse.Namespace) -> tuple[str, ...]:
    return tuple(args.dev_names or ("dev",))


def _run_generate(args: argparse.Namespace) -> int:
    manifest = scan_tree(args.site_dir, args.base_url, dev_versions=_dev_versions(args))
    if not any(entry.preferred for entry in manifest):
        print(
            f"warning: no '{PREFERRED_ALIAS}' symlink in {args.site_dir}, "
            "so no version is marked preferred",
            file=sys.stderr,
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


def _run_deploy(args: argparse.Namespace) -> int:
    aliases = tuple(args.aliases or ())
    sha = deploy(
        args.html_dir,
        args.version,
        args.repo,
        aliases=aliases,
        base_url=args.base_url,
        name=args.name,
        manifest_path=None if args.no_manifest else DEFAULT_MANIFEST,
        source_sha=args.source_sha,
        message=args.message,
        push=args.push,
    )
    _report(sha, "deployed", args, aliases)
    return 0


def _run_promote(args: argparse.Namespace) -> int:
    aliases = tuple(args.aliases or (PREFERRED_ALIAS,))
    sha = promote(
        args.html_dir,
        args.version,
        args.repo,
        aliases=aliases,
        base_url=args.base_url,
        name=args.name,
        source_sha=args.source_sha,
        message=args.message,
        push=args.push,
    )
    _report(sha, "promoted", args, aliases)
    return 0


def _report(
    sha: str, verb: str, args: argparse.Namespace, aliases: Sequence[str]
) -> None:
    summary = f"{sha[:8]} {verb} {args.version} from {args.html_dir}"
    if aliases:
        summary += f" (aliases: {', '.join(aliases)})"
    if args.push:
        summary += ", pushed to origin"
    print(summary)


def _run_retitle(args: argparse.Namespace) -> int:
    sha = retitle(args.version, args.name, args.repo, push=args.push)
    label = f"named {args.name!r}" if args.name else "cleared the name of"
    print(f"{sha[:8]} {label} {args.version}")
    return 0


def _run_delete(args: argparse.Namespace) -> int:
    sha = delete(args.version, args.repo, push=args.push)
    print(f"{sha[:8]} deleted {args.version}")
    return 0


def _run_prune(args: argparse.Namespace) -> int:
    result = prune(
        args.repo, keep=args.keep, keep_since=args.keep_since, push=args.push
    )
    if not result.squashed:
        print(f"nothing to prune: {result.branch} is already {result.kept} commits")
        return 0
    print(
        f"{result.tip[:8]} pruned {result.branch}: {result.squashed} commits squashed "
        f"into a new root, {result.kept} preserved"
    )
    print(
        f"WARNING: history was rewritten; every existing clone of {result.branch} "
        "is now stale",
        file=sys.stderr,
    )
    if args.push:
        print("force-pushed to origin with --force-with-lease", file=sys.stderr)
    else:
        print(
            f"push it with: git push --force-with-lease origin {result.branch}",
            file=sys.stderr,
        )
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
    try:
        return int(args.func(args))
    except (CheckError, DeployError, GitError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
