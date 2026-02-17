from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from justapk.models import AppInfo, DownloadResult
from justapk.sources.base import APKSource
from justapk.utils import HTTP_TIMEOUT, create_cf_session, download_file, log_source, sha256_file


class APKComboSource(APKSource):
    name = "apkcombo"
    BASE = "https://apkcombo.com"

    def __init__(self):
        self.session = create_cf_session()

    def search(self, query: str) -> list[AppInfo]:
        resp = self.session.get(f"{self.BASE}/search/{query}", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        results = []
        seen = set()
        # Links inside .content-apps: /{slug}/{package}/
        for a in soup.select(".content-apps a[href]"):
            href = a.get("href", "").strip("/")
            parts = href.split("/")
            # Pattern: slug/com.example.app
            if len(parts) >= 2 and "." in parts[-1]:
                pkg = parts[-1]
                if pkg in seen:
                    continue
                seen.add(pkg)
                name_el = a.select_one("span.name")
                name = name_el.get_text(strip=True) if name_el else a.get_text(strip=True)
                results.append(AppInfo(
                    package=pkg,
                    name=name,
                    version="",
                    source=self.name,
                ))
        return results

    def _find_slug(self, package: str) -> str | None:
        """Find the APKCombo slug for a package name."""
        resp = self.session.get(f"{self.BASE}/search/{package}", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        for a in soup.select("a[href]"):
            href = a.get("href", "")
            if package in href:
                parts = [p for p in href.split("/") if p]
                # URL pattern: /slug/package
                for i, part in enumerate(parts):
                    if part == package and i > 0:
                        return parts[i - 1]
        return None

    def _find_version_download_url(self, slug: str, package: str, version: str) -> str | None:
        """Find the download page URL for a specific version from old-versions listing."""
        page = 1
        while True:
            url = f"{self.BASE}/{slug}/{package}/old-versions/"
            if page > 1:
                url += f"?page={page}"
            resp = self.session.get(url, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            found_any = False
            for a in soup.select("ul li a[href*='/download/']"):
                found_any = True
                h3 = a.select_one("h3")
                if not h3:
                    continue
                text = h3.get_text(strip=True)
                m = re.search(r'([\d]+(?:\.[\d]+)+)', text)
                if m and m.group(1) == version:
                    href = str(a.get("href", ""))
                    return href if href.startswith("http") else f"{self.BASE}{href}"

            if not found_any:
                break
            next_link = soup.select_one("a[href*='page=']")
            if not next_link or f"page={page + 1}" not in next_link.get("href", ""):
                break
            page += 1

        return None

    def get_info(self, package: str) -> AppInfo | None:
        slug = self._find_slug(package)
        if not slug:
            return None

        resp = self.session.get(f"{self.BASE}/{slug}/{package}", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        name_el = soup.select_one("h1")
        ver_el = soup.select_one(".version")

        return AppInfo(
            package=package,
            name=name_el.get_text(strip=True) if name_el else package,
            version=ver_el.get_text(strip=True) if ver_el else "",
            source=self.name,
        )

    def list_developer_apps(self, developer: str) -> list[AppInfo]:
        from urllib.parse import quote

        apps: list[AppInfo] = []
        seen: set[str] = set()
        page = 1

        while True:
            url = f"{self.BASE}/en/developer/{quote(developer)}/"
            if page > 1:
                url += f"?page={page}"
            resp = self.session.get(url, timeout=HTTP_TIMEOUT)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "lxml")

            found = 0
            for a in soup.select(".content-apps a[href]"):
                href = a.get("href", "").strip("/")
                parts = href.split("/")
                if len(parts) >= 2 and "." in parts[-1]:
                    pkg = parts[-1]
                    if pkg in seen:
                        continue
                    seen.add(pkg)
                    name_el = a.select_one("span.name")
                    name = (
                        name_el.get_text(strip=True) if name_el
                        else a.get_text(strip=True)
                    )
                    apps.append(AppInfo(
                        package=pkg, name=name, version="", source=self.name,
                    ))
                    found += 1

            if found == 0:
                break
            next_link = soup.select_one("a[href*='page=']")
            if not next_link or f"page={page + 1}" not in next_link.get("href", ""):
                break
            page += 1

        return apps

    def list_versions(self, package: str) -> list[tuple[str, str]]:
        slug = self._find_slug(package)
        if not slug:
            return []

        versions: list[tuple[str, str]] = []
        seen: set[str] = set()
        page = 1

        while True:
            url = f"{self.BASE}/{slug}/{package}/old-versions/"
            if page > 1:
                url += f"?page={page}"
            resp = self.session.get(url, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "lxml")

            found = 0
            for a in soup.select("ul li a[href*='/download/']"):
                h3 = a.select_one("h3")
                if not h3:
                    continue
                text = h3.get_text(strip=True)
                # Extract version from "AppName X.Y.Z APK"
                m = re.search(r'([\d]+(?:\.[\d]+)+)', text)
                if m:
                    v = m.group(1)
                    if v not in seen:
                        seen.add(v)
                        date_el = a.select_one("span.date")
                        date_str = date_el.get_text(strip=True) if date_el else ""
                        versions.append((v, date_str))
                        found += 1

            # Check for next page
            if found == 0:
                break
            next_link = soup.select_one("a[href*='page=']")
            if not next_link or f"page={page + 1}" not in next_link.get("href", ""):
                break
            page += 1

        return versions

    def download(self, package: str, output_dir: Path, version: str | None = None) -> DownloadResult:
        slug = self._find_slug(package)
        if not slug:
            raise RuntimeError(f"[apkcombo] Package not found: {package}")

        # Download page URL — for a specific version, find its URL from old-versions
        if version:
            page_url = self._find_version_download_url(slug, package, version)
            if not page_url:
                raise RuntimeError(f"[apkcombo] Version {version} not found for {package}")
        else:
            page_url = f"{self.BASE}/{slug}/{package}/download/apk"

        resp = self.session.get(page_url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Get version from page
        if not version:
            ver_el = soup.select_one(".version")
            if ver_el:
                version = ver_el.get_text(strip=True)

        # Find R2 download links: /r2?u={url_encoded_signed_url}
        dl_url = None
        file_type = "apk"
        for a in soup.select("a[href*='/r2?']"):
            href = a.get("href", "")
            if "/r2?" in href:
                variant_text = a.get_text(strip=True).lower()
                dl_url = href if href.startswith("http") else f"{self.BASE}{href}"

                if not version:
                    m = re.search(r'([\d.]+)\s*\(', a.get_text(strip=True))
                    if m:
                        version = m.group(1)

                file_type = "xapk" if "xapk" in variant_text else "apk"

                # Prefer APK over XAPK
                if "apk" in variant_text and "xapk" not in variant_text:
                    break

        if not dl_url:
            raise RuntimeError(f"[apkcombo] No download link on: {page_url}")

        ver = version or "latest"
        filename = f"{package}-{ver}.{file_type}"
        out_path = output_dir / filename

        log_source(self.name, f"Downloading {package} v{ver}")
        # APKCombo R2: HEAD=403, only GET works; follow redirects
        size = download_file(dl_url, out_path, self.session)

        return DownloadResult(
            path=out_path,
            package=package,
            version=ver,
            source=self.name,
            size=size,
            sha256=sha256_file(out_path),
        )
