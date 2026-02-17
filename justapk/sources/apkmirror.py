from __future__ import annotations

import re
from pathlib import Path

from bs4 import BeautifulSoup

from justapk.models import AppInfo, DownloadResult
from justapk.sources.base import APKSource
from justapk.utils import HTTP_TIMEOUT, create_cf_session, download_file, log_source, sha256_file


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

    def _find_app_slug(self, package: str) -> str | None:
        """Extract the app slug from a release page URL.

        APKMirror URLs follow: /apk/<developer>/<app-slug>/<release-slug>/
        We need <app-slug> for the uploads page.
        """
        release_url = self._search_app(package)
        if not release_url:
            return None
        # Parse: https://www.apkmirror.com/apk/developer/app-slug/release-slug/
        parts = release_url.rstrip("/").split("/")
        # Expected: ['https:', '', 'www.apkmirror.com', 'apk', developer, app-slug, ...]
        if len(parts) >= 6 and parts[3] == "apk":
            return parts[5]
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

    def list_developer_apps(self, developer: str) -> list[AppInfo]:
        slug = re.sub(r'[^a-z0-9]+', '-', developer.lower()).strip('-')
        apps: list[AppInfo] = []
        seen: set[str] = set()
        url: str | None = f"{self.BASE}/apk/{slug}/"

        while url:
            resp = self.session.get(url, timeout=HTTP_TIMEOUT)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "lxml")

            for row in soup.select(".appRow"):
                a = row.select_one("h5 a[href]")
                if not a:
                    continue
                name = a.get_text(strip=True)
                href = a.get("href", "")
                if href in seen:
                    continue
                seen.add(href)

                # Resolve the real package name from the app page
                app_url = f"{self.BASE}{href}" if not href.startswith("http") else href
                try:
                    app_resp = self.session.get(app_url, timeout=HTTP_TIMEOUT)
                    if app_resp.status_code == 200:
                        pkg_m = re.search(
                            r'<span[^>]*>\s*([\w.]+(?:\.[\w]+){2,})\s*</span>',
                            app_resp.text,
                        )
                        if not pkg_m:
                            pkg_m = re.search(r'id=([\w.]+(?:\.[\w]+){2,})', app_resp.text)
                        pkg = pkg_m.group(1) if pkg_m else href.strip("/").split("/")[-1]
                    else:
                        pkg = href.strip("/").split("/")[-1]
                except Exception:
                    pkg = href.strip("/").split("/")[-1]

                apps.append(AppInfo(
                    package=pkg, name=name, version="", source=self.name,
                ))

            next_link = soup.select_one("a.nextpostslink[href]")
            if next_link:
                href = next_link.get("href", "")
                url = f"{self.BASE}{href}" if not href.startswith("http") else href
            else:
                url = None

        return apps

    def list_versions(self, package: str) -> list[tuple[str, str]]:
        slug = self._find_app_slug(package)
        if not slug:
            return []

        versions: list[tuple[str, str]] = []
        seen: set[str] = set()
        url: str | None = f"{self.BASE}/uploads/?appcategory={slug}"

        while url:
            resp = self.session.get(url, timeout=HTTP_TIMEOUT)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "lxml")

            for row in soup.select(".appRow"):
                a = row.select_one("h5 a")
                if not a:
                    continue
                text = a.get_text(strip=True)
                m = re.search(r"([\d]+\.[\d]+[\d.]*)", text)
                if m:
                    v = m.group(1)
                    if v not in seen:
                        seen.add(v)
                        date_el = row.select_one(".dateyear_utc")
                        date_str = date_el.get_text(strip=True) if date_el else ""
                        versions.append((v, date_str))

            # Follow pagination
            next_link = soup.select_one("a.nextpostslink[href]")
            if next_link:
                href = next_link.get("href", "")
                url = f"{self.BASE}{href}" if not href.startswith("http") else href
            else:
                url = None

        return versions

    def _find_release_url_for_version(self, package: str, version: str) -> str | None:
        """Find the release page URL for a specific version."""
        slug = self._find_app_slug(package)
        if not slug:
            return None

        url: str | None = f"{self.BASE}/uploads/?appcategory={slug}"
        while url:
            resp = self.session.get(url, timeout=HTTP_TIMEOUT)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "lxml")

            for row in soup.select(".appRow"):
                a = row.select_one("h5 a[href]")
                if not a:
                    continue
                text = a.get_text(strip=True)
                if version in text:
                    href = str(a.get("href", ""))
                    return f"{self.BASE}{href}" if not href.startswith("http") else href

            next_link = soup.select_one("a.nextpostslink[href]")
            if next_link:
                href = next_link.get("href", "")
                url = f"{self.BASE}{href}" if not href.startswith("http") else href
            else:
                url = None

        return None

    def download(self, package: str, output_dir: Path, version: str | None = None) -> DownloadResult:
        # Step 1: Find the release page (version-specific or latest)
        if version:
            release_url = self._find_release_url_for_version(package, version)
            if not release_url:
                raise RuntimeError(f"[apkmirror] Version {version} not found for: {package}")
        else:
            release_url = self._search_app(package)
            if not release_url:
                raise RuntimeError(f"[apkmirror] Package not found: {package}")

        resp = self.session.get(release_url, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()

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

        log_source(self.name, f"Downloading {package} v{ver}")
        size = download_file(apk_url, out_path, self.session)

        return DownloadResult(
            path=out_path,
            package=package,
            version=ver,
            source=self.name,
            size=size,
            sha256=sha256_file(out_path),
        )
