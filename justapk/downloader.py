from __future__ import annotations

import re
import time
from pathlib import Path

from justapk.models import AppInfo, DownloadResult
from justapk.sources import SOURCE_PRIORITY, SOURCE_REGISTRY
from justapk.sources.base import APKSource
from justapk.utils import (
    format_elapsed,
    format_size,
    log_fail,
    log_header,
    log_info,
    log_ok,
    log_step,
    sha256_file,
)
from justapk.xapk import convert_xapk_to_apk


def _version_key(version: str) -> tuple:
    """Parse a version string into a tuple of ints for sorting."""
    parts = re.split(r'[.\-_+]', version)
    result = []
    for p in parts:
        try:
            result.append((0, int(p)))
        except ValueError:
            result.append((1, p))
    return tuple(result)


def _sort_versions_desc(version_source: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    """Return version_source dict sorted from latest to oldest."""
    sorted_keys = sorted(version_source.keys(), key=_version_key, reverse=True)
    return {k: version_source[k] for k in sorted_keys}


class APKDownloader:
    def __init__(self, sources: list[str] | None = None, auto_convert_xapk: bool = True):
        self._source_names = [n for n in (sources or SOURCE_PRIORITY) if n in SOURCE_REGISTRY]
        self._source_instances: dict[str, APKSource] = {}
        self._auto_convert_xapk = auto_convert_xapk

    @property
    def source_names(self) -> list[str]:
        return list(self._source_names)

    def _get_source(self, name: str) -> APKSource:
        """Get a source by name, instantiating lazily. Raises ValueError if unknown."""
        if name not in self._source_names:
            raise ValueError(f"Unknown source: {name}. Available: {self._source_names}")
        if name not in self._source_instances:
            self._source_instances[name] = SOURCE_REGISTRY[name]()
        return self._source_instances[name]

    def _iter_sources(self) -> list[tuple[str, APKSource]]:
        """Iterate all sources in priority order, instantiating lazily."""
        return [(name, self._get_source(name)) for name in self._source_names]

    def download(
        self,
        package: str,
        output_dir: Path | None = None,
        source: str | None = None,
        version: str | None = None,
    ) -> DownloadResult:
        output_dir = output_dir or Path(".")
        output_dir.mkdir(parents=True, exist_ok=True)

        if source:
            result = self._get_source(source).download(package, output_dir, version)
            return self._maybe_convert_xapk(result)

        # Fallback through sources
        errors: list[tuple[str, str]] = []
        sources = self._iter_sources()
        total = len(sources)
        for i, (name, src) in enumerate(sources, 1):
            try:
                log_step(i, total, f"Trying {name}...")
                result = src.download(package, output_dir, version)
                return self._maybe_convert_xapk(result)
            except Exception as e:
                log_fail(f"{name}: {e}")
                errors.append((name, str(e)))

        error_summary = "; ".join(f"{n}: {e}" for n, e in errors)
        raise RuntimeError(f"All sources failed for {package}: {error_summary}")

    def list_all_versions(
        self,
        package: str,
        source: str | None = None,
    ) -> dict[str, tuple[str, str]]:
        """Collect all available versions from sources.

        Returns ``{version: (source_name, date_str)}``.
        """
        version_source: dict[str, tuple[str, str]] = {}

        log_header(f"Scanning versions for {package}")
        if source:
            src_obj = self._get_source(source)
            log_step(1, 1, f"Querying {source}...")
            for v, date in src_obj.list_versions(package):
                if v not in version_source:
                    version_source[v] = (source, date)
        else:
            sources = self._iter_sources()
            for i, (name, src) in enumerate(sources, 1):
                try:
                    log_step(i, len(sources), f"Querying {name}...")
                    for v, date in src.list_versions(package):
                        if v not in version_source:
                            version_source[v] = (name, date)
                except Exception as e:
                    log_fail(f"{name}: {e}")

        if not version_source:
            raise RuntimeError(f"No versions found for {package}")

        # Sort versions from latest to oldest
        version_source = _sort_versions_desc(version_source)

        log_ok(f"Found {len(version_source)} version(s)")
        return version_source

    def download_versions(
        self,
        package: str,
        version_source: dict[str, tuple[str, str]],
        output_dir: Path | None = None,
    ) -> list[DownloadResult]:
        """Download specific versions given a {version: (source_name, date)} mapping."""
        output_dir = output_dir or Path(".")
        output_dir.mkdir(parents=True, exist_ok=True)

        total = len(version_source)
        log_header(f"Downloading {total} version(s)")
        t0 = time.monotonic()
        results: list[DownloadResult] = []
        failed = 0
        for i, (ver, (src_name, _date)) in enumerate(version_source.items(), 1):
            try:
                log_step(i, total, f"v{ver} from {src_name}")
                result = self.download(package, output_dir, source=src_name, version=ver)
                results.append(result)
                log_ok(f"{result.path.name} ({format_size(result.size)})")
            except Exception as e:
                log_fail(f"v{ver}: {e}")
                failed += 1

        elapsed = time.monotonic() - t0
        total_size = sum(r.size for r in results)
        log_header("Done")
        log_info(
            f"{len(results)} downloaded, {failed} failed, "
            f"{format_size(total_size)} total in {format_elapsed(elapsed)}"
        )

        if not results:
            raise RuntimeError(f"Failed to download any version of {package}")

        return results

    def download_all(
        self,
        package: str,
        output_dir: Path | None = None,
        source: str | None = None,
    ) -> list[DownloadResult]:
        """Download all available versions of a package."""
        version_source = self.list_all_versions(package, source)
        return self.download_versions(package, version_source, output_dir)

    def _maybe_convert_xapk(self, result: DownloadResult) -> DownloadResult:
        """Auto-convert XAPK/split APK to regular APK."""
        if not self._auto_convert_xapk:
            return result
        suffix = result.path.suffix.lower()
        if suffix not in (".xapk", ".apks"):
            return result
        log_info(f"Converting {result.path.name} to APK...")
        apk_path = convert_xapk_to_apk(result.path, result.path.parent)
        result.path.unlink(missing_ok=True)
        return DownloadResult(
            path=apk_path,
            package=result.package,
            version=result.version,
            source=result.source,
            size=apk_path.stat().st_size,
            sha256=sha256_file(apk_path),
        )

    def developer_apps(
        self,
        developer: str,
        source: str | None = None,
    ) -> list[AppInfo]:
        """List all apps from a developer across sources."""
        if source:
            src_obj = self._get_source(source)
            log_step(1, 1, f"Querying {source}...")
            return src_obj.list_developer_apps(developer)

        results: list[AppInfo] = []
        seen_pkgs: set[str] = set()
        sources = self._iter_sources()
        for i, (name, src) in enumerate(sources, 1):
            try:
                log_step(i, len(sources), f"Querying {name}...")
                for app in src.list_developer_apps(developer):
                    if app.package not in seen_pkgs:
                        seen_pkgs.add(app.package)
                        results.append(app)
            except Exception as e:
                log_fail(f"{name}: {e}")
        return results

    def download_developer(
        self,
        developer: str,
        output_dir: Path | None = None,
        source: str | None = None,
    ) -> list[DownloadResult]:
        """Download all apps from a developer."""
        output_dir = output_dir or Path(".")
        output_dir.mkdir(parents=True, exist_ok=True)

        log_header(f"Listing apps for developer: {developer}")
        apps = self.developer_apps(developer, source=source)
        if not apps:
            raise RuntimeError(f"No apps found for developer: {developer}")

        log_ok(f"Found {len(apps)} app(s)")
        log_header(f"Downloading {len(apps)} app(s)")

        t0 = time.monotonic()
        results: list[DownloadResult] = []
        failed = 0
        for i, app in enumerate(apps, 1):
            try:
                log_step(i, len(apps), f"{app.name} ({app.package})")
                result = self.download(
                    app.package, output_dir, source=app.source or source,
                )
                results.append(result)
                log_ok(f"{result.path.name} ({format_size(result.size)})")
            except Exception as e:
                log_fail(f"{app.package}: {e}")
                failed += 1

        elapsed = time.monotonic() - t0
        total_size = sum(r.size for r in results)
        log_header("Done")
        log_info(
            f"{len(results)} downloaded, {failed} failed, "
            f"{format_size(total_size)} total in {format_elapsed(elapsed)}"
        )

        if not results:
            raise RuntimeError(f"Failed to download any app for developer: {developer}")

        return results

    def search(self, query: str, source: str | None = None) -> list[AppInfo]:
        if source:
            return self._get_source(source).search(query)

        results: list[AppInfo] = []
        seen_pkgs: set[tuple[str, str]] = set()
        sources = self._iter_sources()
        for i, (name, src) in enumerate(sources, 1):
            try:
                log_step(i, len(sources), f"Searching {name}...")
                for app in src.search(query):
                    key = (app.package, app.source)
                    if key not in seen_pkgs:
                        seen_pkgs.add(key)
                        results.append(app)
            except Exception as e:
                log_fail(f"{name}: {e}")
        return results

    def info(self, package: str, source: str | None = None) -> AppInfo | None:
        if source:
            return self._get_source(source).get_info(package)

        sources = self._iter_sources()
        for i, (name, src) in enumerate(sources, 1):
            try:
                log_step(i, len(sources), f"Querying {name}...")
                result = src.get_info(package)
                if result:
                    return result
            except Exception as e:
                log_fail(f"{name}: {e}")
        return None
