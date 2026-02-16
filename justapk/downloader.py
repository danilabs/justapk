from __future__ import annotations

import sys
from pathlib import Path

from justapk.models import AppInfo, DownloadResult
from justapk.sources import SOURCE_PRIORITY, SOURCE_REGISTRY
from justapk.xapk import convert_xapk_to_apk


class APKDownloader:
    def __init__(self, sources: list[str] | None = None, auto_convert_xapk: bool = True):
        priority = sources or SOURCE_PRIORITY
        self._sources = {}
        for name in priority:
            if name in SOURCE_REGISTRY:
                self._sources[name] = SOURCE_REGISTRY[name]()
        self._auto_convert_xapk = auto_convert_xapk

    @property
    def source_names(self) -> list[str]:
        return list(self._sources.keys())

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
            if source not in self._sources:
                raise ValueError(f"Unknown source: {source}. Available: {list(self._sources.keys())}")
            result = self._sources[source].download(package, output_dir, version)
            return self._maybe_convert_xapk(result)

        # Fallback through sources
        errors = []
        for name, src in self._sources.items():
            try:
                sys.stderr.write(f"Trying {name}...\n")
                result = src.download(package, output_dir, version)
                return self._maybe_convert_xapk(result)
            except Exception as e:
                sys.stderr.write(f"  {name} failed: {e}\n")
                errors.append((name, str(e)))

        error_summary = "; ".join(f"{n}: {e}" for n, e in errors)
        raise RuntimeError(f"All sources failed for {package}: {error_summary}")

    def _maybe_convert_xapk(self, result: DownloadResult) -> DownloadResult:
        """Auto-convert XAPK/split APK to regular APK."""
        if not self._auto_convert_xapk:
            return result
        suffix = result.path.suffix.lower()
        if suffix not in (".xapk", ".apks"):
            return result
        sys.stderr.write(f"[xapk] Converting {result.path.name} to APK...\n")
        apk_path = convert_xapk_to_apk(result.path, result.path.parent)
        # Remove original XAPK
        result.path.unlink(missing_ok=True)
        from justapk.utils import sha256_file
        return DownloadResult(
            path=apk_path,
            package=result.package,
            version=result.version,
            source=result.source,
            size=apk_path.stat().st_size,
            sha256=sha256_file(apk_path),
        )

    def search(self, query: str, source: str | None = None) -> list[AppInfo]:
        if source:
            if source not in self._sources:
                raise ValueError(f"Unknown source: {source}")
            return self._sources[source].search(query)

        # Aggregate from all sources
        results = []
        seen_pkgs = set()
        for name, src in self._sources.items():
            try:
                for app in src.search(query):
                    key = (app.package, app.source)
                    if key not in seen_pkgs:
                        seen_pkgs.add(key)
                        results.append(app)
            except Exception as e:
                sys.stderr.write(f"  {name} search failed: {e}\n")
        return results

    def info(self, package: str, source: str | None = None) -> AppInfo | None:
        if source:
            if source not in self._sources:
                raise ValueError(f"Unknown source: {source}")
            return self._sources[source].get_info(package)

        for name, src in self._sources.items():
            try:
                result = src.get_info(package)
                if result:
                    return result
            except Exception as e:
                sys.stderr.write(f"  {name} info failed: {e}\n")
        return None
