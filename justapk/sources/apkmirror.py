from __future__ import annotations

import re
import sys
from pathlib import Path

from bs4 import BeautifulSoup

from justapk.models import AppInfo, DownloadResult
from justapk.sources.base import APKSource
from justapk.utils import HTTP_TIMEOUT, create_cf_session, download_file, sha256_file


class APKMirrorSource(APKSource):
    name = "apkmirror"
    BASE = "https://www.apkmirror.com"

    def __init__(self):
        self.session = create_cf_session()

    def search(self, query: str) -> list[AppInfo]:
        resp = self.session.get(
            f"{self.BASE}/",
            params={"post_type": "app_listing", "searchtype": "apk", "s": query},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        results = []
        seen = set()
        for row in soup.select(".appRow"):
            a = row.select_one("h5 a[href]")
            if not a:
                continue
            name = a.get_text(strip=True)
            href = a.get("href", "")
            if href in seen:
                continue
            seen.add(href)
            dev_el = row.select_one(".byDeveloper")
            results.append(AppInfo(
                package=href.strip("/").split("/")[-1],
                name=name,
                version="",
                source=self.name,
                description=dev_el.get_text(strip=True) if dev_el else "",
            ))
        return results

    def _search_app(self, package: str) -> str | None:
        """Find the latest release page URL for a package."""
        resp = self.session.get(
            f"{self.BASE}/",
            params={"post_type": "app_listing", "searchtype": "apk", "s": package},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        for row in soup.select(".appRow"):
            a = row.select_one("h5 a[href]")
            if a:
                href = str(a.get("href", ""))
                return f"{self.BASE}{href}" if not href.startswith("http") else href
        return None

    def get_info(self, package: str) -> AppInfo | None:
        release_url = self._search_app(package)
        if not release_url:
            return None

        resp = self.session.get(release_url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()

        # Verify page actually belongs to this package
        if package not in resp.text:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        name_el = soup.select_one("h1")
        ver = ""
        if name_el:
            m = re.search(r"([\d.]+)", name_el.get_text())
            if m:
                ver = m.group(1)

        return AppInfo(
            package=package,
            name=name_el.get_text(strip=True) if name_el else package,
            version=ver,
            source=self.name,
        )

    def download(self, package: str, output_dir: Path, version: str | None = None) -> DownloadResult:
        # Step 1: Find the release page
        release_url = self._search_app(package)
        if not release_url:
            raise RuntimeError(f"[apkmirror] Package not found: {package}")

        resp = self.session.get(release_url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()

        # Verify page actually belongs to this package
        if package not in resp.text:
            raise RuntimeError(f"[apkmirror] Package not found: {package}")

        soup = BeautifulSoup(resp.text, "lxml")

        # Extract version from title
        ver = version or ""
        if not ver:
            h1 = soup.select_one("h1")
            if h1:
                m = re.search(r"([\d.]+)", h1.get_text())
                if m:
                    ver = m.group(1)

        # Step 2: Find APK variant link (prefer universal)
        variant_url = None
        for row in soup.select(".variants-table .table-row"):
            a = row.select_one("a.accent_color[href]")
            if not a:
                continue
            text = row.get_text(strip=True).lower()
            href = a.get("href", "")
            variant_url = f"{self.BASE}{href}" if not href.startswith("http") else href
            if "universal" in text or "nodpi" in text:
                break  # Prefer universal

        if not variant_url:
            raise RuntimeError(f"[apkmirror] No APK variant found for: {package}")

        # Step 3: Get variant page → download button
        resp = self.session.get(variant_url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        dl_btn = soup.select_one("a.downloadButton[href]")
        if not dl_btn:
            raise RuntimeError("[apkmirror] No download button on variant page")

        dl_page_href = dl_btn.get("href", "")
        dl_page_url = f"{self.BASE}{dl_page_href}" if not dl_page_href.startswith("http") else dl_page_href

        # Step 4: Download confirmation page → key link
        resp = self.session.get(dl_page_url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        apk_url = None
        for a in soup.select("a[href*='key=']"):
            href = a.get("href", "")
            if "download" in href:
                apk_url = f"{self.BASE}{href}" if not href.startswith("http") else href
                break

        if not apk_url:
            raise RuntimeError("[apkmirror] No final download link found")

        ver = ver or "latest"
        filename = f"{package}-{ver}.apk"
        out_path = output_dir / filename

        sys.stderr.write(f"[apkmirror] Downloading {package} v{ver}\n")
        size = download_file(apk_url, out_path, self.session)

        return DownloadResult(
            path=out_path,
            package=package,
            version=ver,
            source=self.name,
            size=size,
            sha256=sha256_file(out_path),
        )
