from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from justapk import __version__
from justapk.downloader import APKDownloader
from justapk.sources import SOURCE_PRIORITY
from justapk.utils import format_size, log_fail, log_header, log_info, log_ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="justapk",
        description="Multi-source APK downloader",
    )
    parser.add_argument("-V", "--version", action="version", version=f"justapk {__version__}")
    sub = parser.add_subparsers(dest="command")

    # download
    dl = sub.add_parser("download", help="Download APK by package name")
    dl.add_argument("package", help="Android package name (e.g. org.telegram.messenger)")
    dl.add_argument("-s", "--source", choices=SOURCE_PRIORITY, help="Use specific source")
    dl.add_argument("-o", "--output", type=Path, default=Path("."), help="Output directory")
    dl.add_argument(
        "-v", "--app-version", dest="app_version",
        help="Specific version to download (use 'all' to list and choose interactively)",
    )
    dl.add_argument(
        "--no-convert", action="store_true",
        help="Do not auto-convert XAPK/split APK to APK",
    )

    # search
    sr = sub.add_parser("search", help="Search for apps")
    sr.add_argument("query", help="Search query")
    sr.add_argument("-s", "--source", choices=SOURCE_PRIORITY, help="Search in specific source")

    # info
    inf = sub.add_parser("info", help="Get app metadata")
    inf.add_argument("package", help="Android package name")
    inf.add_argument("-s", "--source", choices=SOURCE_PRIORITY, help="Use specific source")

    # convert
    cv = sub.add_parser("convert", help="Convert XAPK/split APK to single APK")
    cv.add_argument("file", type=Path, help="Path to .xapk or .apks file")
    cv.add_argument("-o", "--output", type=Path, help="Output directory (default: same as input)")

    # sources
    sub.add_parser("sources", help="List available sources")

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    try:
        if args.command == "sources":
            return _cmd_sources()
        elif args.command == "download":
            return _cmd_download(args)
        elif args.command == "search":
            return _cmd_search(args)
        elif args.command == "info":
            return _cmd_info(args)
        elif args.command == "convert":
            return _cmd_convert(args)
    except KeyboardInterrupt:
        sys.stderr.write("\n")
        log_fail("Interrupted")
        return 130
    except (RuntimeError, ValueError) as e:
        log_fail(str(e))
        return 1

    return 1


def _cmd_sources() -> int:
    data = [{"name": name, "priority": i + 1} for i, name in enumerate(SOURCE_PRIORITY)]
    print(json.dumps(data, indent=2))
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    dl = APKDownloader(auto_convert_xapk=not args.no_convert)
    app_version = args.app_version

    if app_version and app_version.lower() == "all":
        # Interactive multi-version download — scan first, then pick
        version_source = dl.list_all_versions(
            package=args.package,
            source=args.source,
        )

        version_source = _interactive_pick(version_source)
        if not version_source:
            log_info("No versions selected")
            return 0

        results = dl.download_versions(
            package=args.package,
            version_source=version_source,
            output_dir=args.output,
        )
        print(json.dumps([r.to_dict() for r in results], indent=2))
    else:
        log_header(f"Downloading {args.package}")
        result = dl.download(
            package=args.package,
            output_dir=args.output,
            source=args.source,
            version=app_version,
        )
        log_ok(f"{result.path.name} ({format_size(result.size)}) from {result.source}")
        print(json.dumps(result.to_dict(), indent=2))
    return 0


def _interactive_pick(version_source: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """Interactive version picker. Returns selected subset."""
    versions = list(version_source.items())  # [(ver, (source, date)), ...]

    # Compute column widths
    w_num = len(str(len(versions)))
    w_ver = max(len(v) for v, _ in versions)
    w_ver = max(w_ver, 7)  # min width for "Version"
    w_date = max((len(d) for _, (_, d) in versions), default=4)
    w_date = max(w_date, 4)  # min width for "Date"
    w_src = max((len(s) for _, (s, _) in versions), default=6)
    w_src = max(w_src, 6)  # min width for "Source"

    # Table header
    sys.stderr.write("\n")
    header = f"  {'#':>{w_num}}  {'Version':<{w_ver}}  {'Date':<{w_date}}  {'Source':<{w_src}}"
    sep = f"  {'-' * w_num}  {'-' * w_ver}  {'-' * w_date}  {'-' * w_src}"
    sys.stderr.write(header + "\n")
    sys.stderr.write(sep + "\n")

    for i, (ver, (src, date)) in enumerate(versions, 1):
        sys.stderr.write(
            f"  {i:>{w_num}}  {ver:<{w_ver}}  {date or '-':<{w_date}}  {src:<{w_src}}\n"
        )

    sys.stderr.write("\n  Enter version numbers to download (e.g. 1,3,5-10 or 'all'):\n")
    sys.stderr.write("  > ")
    sys.stderr.flush()

    try:
        user_input = input().strip()
    except EOFError:
        return {}

    if not user_input:
        return {}

    if user_input.lower() == "all":
        return version_source

    # Parse selection: supports 1,3,5-10
    selected_indices: set[int] = set()
    for part in user_input.split(","):
        part = part.strip()
        if "-" in part:
            range_parts = part.split("-", 1)
            try:
                start = int(range_parts[0].strip())
                end = int(range_parts[1].strip())
                selected_indices.update(range(start, end + 1))
            except ValueError:
                log_fail(f"Invalid range: {part}")
                return {}
        else:
            try:
                selected_indices.add(int(part))
            except ValueError:
                log_fail(f"Invalid number: {part}")
                return {}

    # Build filtered dict preserving order
    result: dict[str, tuple[str, str]] = {}
    for idx in sorted(selected_indices):
        if 1 <= idx <= len(versions):
            ver, val = versions[idx - 1]
            result[ver] = val

    if result:
        log_ok(f"Selected {len(result)} version(s)")
    return result


def _cmd_search(args: argparse.Namespace) -> int:
    dl = APKDownloader()
    log_header(f"Searching for \"{args.query}\"")
    results = dl.search(args.query, source=args.source)
    if not results:
        log_fail(f"No results for: {args.query}")
        return 1
    log_ok(f"Found {len(results)} result(s)")
    data = [r.to_dict() for r in results]
    print(json.dumps(data, indent=2))
    return 0


def _cmd_convert(args: argparse.Namespace) -> int:
    from justapk.utils import sha256_file
    from justapk.xapk import convert_xapk_to_apk

    xapk = args.file
    if not xapk.exists():
        log_fail(f"File not found: {xapk}")
        return 1
    if xapk.suffix.lower() not in (".xapk", ".apks"):
        log_fail(f"Not an XAPK/APKS file: {xapk}")
        return 1

    log_header(f"Converting {xapk.name}")
    out_dir = args.output or xapk.parent
    apk_path = convert_xapk_to_apk(xapk, out_dir)
    size = apk_path.stat().st_size
    log_ok(f"{apk_path.name} ({format_size(size)})")
    print(json.dumps({
        "path": str(apk_path),
        "size": size,
        "sha256": sha256_file(apk_path),
    }, indent=2))
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    dl = APKDownloader()
    log_header(f"Looking up {args.package}")
    result = dl.info(args.package, source=args.source)
    if result:
        log_ok(f"{result.name} v{result.version} ({result.source})")
        print(json.dumps(result.to_dict(), indent=2))
        return 0
    log_fail(f"No info found for: {args.package}")
    return 1
