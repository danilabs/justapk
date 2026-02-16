from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

from justapk.models import AppInfo, DownloadResult
from justapk.sources.base import APKSource
from justapk.utils import HTTP_TIMEOUT, create_session, download_file, sha256_file

# F-Droid JSON API — no HTML scraping needed
_API_BASE = "https://f-droid.org/api/v1"
_REPO_BASE = "https://f-droid.org/repo"
_SEARCH_URL = "https://search.f-droid.org/"


class FDroidSource(APKSource):
    name = "fdroid"

    def __init__(self):
        self.session = create_session()

    def search(self, query: str) -> list[AppInfo]:
        resp = self.session.get(_SEARCH_URL, params={"q": query}, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        results = []
        for item in soup.select("a[href*='/packages/']"):
            href = item.get("href", "")
            m = re.search(r"/packages/([^/]+)/?", href)
            if not m:
                continue
            pkg = m.group(1)
            name_el = item.select_one("h4.package-name, .package-name")
            name = name_el.get_text(strip=True) if name_el else pkg
            results.append(AppInfo(
                package=pkg,
                name=name,
                version="",
                source=self.name,
            ))
        return results

    def get_info(self, package: str) -> AppInfo | None:
        data = self._get_package_json(package)
        if not data:
            return None
        packages = data.get("packages", [])
        latest = packages[0] if packages else {}
        return AppInfo(
            package=package,
            name=package,
            version=latest.get("versionName", ""),
            source=self.name,
        )

    def _get_package_json(self, package: str) -> dict | None:
        """Fetch package info from F-Droid JSON API."""
        resp = self.session.get(f"{_API_BASE}/packages/{package}", timeout=HTTP_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    def download(self, package: str, output_dir: Path, version: str | None = None) -> DownloadResult:
        data = self._get_package_json(package)
        if not data:
            raise RuntimeError(f"[fdroid] Package not found: {package}")

        packages = data.get("packages", [])
        if not packages:
            raise RuntimeError(f"[fdroid] No versions for: {package}")

        # Find matching version or use suggested
        version_code = data.get("suggestedVersionCode")
        version_name = ""

        if version:
            for pkg in packages:
                if pkg.get("versionName") == version:
                    version_code = pkg["versionCode"]
                    version_name = version
                    break
            if not version_name:
                available = [p.get("versionName", "?") for p in packages[:5]]
                raise RuntimeError(
                    f"[fdroid] Version {version} not found for {package}. "
                    f"Available: {', '.join(available)}"
                )
        if not version_name:
            version_code = version_code or packages[0]["versionCode"]
            for pkg in packages:
                if pkg["versionCode"] == version_code:
                    version_name = pkg.get("versionName", str(version_code))
                    break

        # Direct download URL: predictable pattern
        url = f"{_REPO_BASE}/{package}_{version_code}.apk"
        filename = f"{package}-{version_name}.apk"
        out_path = output_dir / filename

        sys.stderr.write(f"[fdroid] Downloading {package} v{version_name}\n")
        size = download_file(url, out_path, self.session)

        return DownloadResult(
            path=out_path,
            package=package,
            version=version_name,
            source=self.name,
            size=size,
            sha256=sha256_file(out_path),
        )
