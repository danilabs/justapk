from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from justapk import __version__
from justapk.downloader import APKDownloader
from justapk.sources import SOURCE_PRIORITY


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
    dl.add_argument("-v", "--app-version", dest="app_version", help="Specific version to download")
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
        sys.stderr.write("\nInterrupted.\n")
        return 130
    except Exception as e:
        sys.stderr.write(f"Error: {e}\n")

    return 1


def _cmd_sources() -> int:
    data = [{"name": name, "priority": i + 1} for i, name in enumerate(SOURCE_PRIORITY)]
    print(json.dumps(data, indent=2))
    return 0


def _cmd_download(args) -> int:
    dl = APKDownloader(auto_convert_xapk=not args.no_convert)
    result = dl.download(
        package=args.package,
        output_dir=args.output,
        source=args.source,
        version=args.app_version,
    )
    print(json.dumps(result.to_dict(), indent=2))
    return 0


def _cmd_search(args) -> int:
    dl = APKDownloader()
    results = dl.search(args.query, source=args.source)
    data = [r.to_dict() for r in results]
    print(json.dumps(data, indent=2))
    return 0


def _cmd_convert(args) -> int:
    from justapk.utils import sha256_file
    from justapk.xapk import convert_xapk_to_apk

    xapk = args.file
    if not xapk.exists():
        sys.stderr.write(f"File not found: {xapk}\n")
        return 1
    if xapk.suffix.lower() not in (".xapk", ".apks"):
        sys.stderr.write(f"Not an XAPK/APKS file: {xapk}\n")
        return 1

    out_dir = args.output or xapk.parent
    apk_path = convert_xapk_to_apk(xapk, out_dir)
    print(json.dumps({
        "path": str(apk_path),
        "size": apk_path.stat().st_size,
        "sha256": sha256_file(apk_path),
    }, indent=2))
    return 0


def _cmd_info(args) -> int:
    dl = APKDownloader()
    result = dl.info(args.package, source=args.source)
    if result:
        print(json.dumps(result.to_dict(), indent=2))
        return 0
    else:
        sys.stderr.write(f"No info found for: {args.package}\n")
        return 1
