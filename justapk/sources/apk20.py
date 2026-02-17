from __future__ import annotations

import json
import re
from pathlib import Path

from bs4 import BeautifulSoup

from justapk.models import AppInfo, DownloadResult
from justapk.sources.base import APKSource
from justapk.utils import HTTP_TIMEOUT, create_session, download_file, log_source, sha256_file


class APK20Source(APKSource):
    name = "apk20"
    BASE = "https://www.apk20.com"
    FILE_SERVER = "https://srv01.apk20.com"

    def __init__(self):
        self.session = create_session()

    def _parse_rsc_apps(self, text: str) -> list[dict]:
        """Parse Next.js RSC payload for app objects."""
        results = []
        seen: set[str] = set()
        for m in re.finditer(r'self\.__next_f\.push\(\[1,"(.+?)"\]\)', text):
            chunk = m.group(1).replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
            for arr_m in re.finditer(
                r'\[(\{[^]]*"packageName"[^]]*\}'
                r'(?:,\{[^]]*"packageName"[^]]*\})*)\]',
                chunk,
            ):
                try:
                    data = json.loads(f"[{arr_m.group(1)}]")
                    for item in data:
                        pkg = item.get("packageName", "")
                        if pkg and pkg not in seen:
                            seen.add(pkg)
                            results.append(item)
                except (json.JSONDecodeError, ValueError):
                    pass
            for obj_m in re.finditer(r'\{"packageName":"[^"]+?"[^}]*\}', chunk):
                try:
                    item = json.loads(obj_m.group(0))
                    pkg = item.get("packageName", "")
                    if pkg and pkg not in seen:
                        seen.add(pkg)
                        results.append(item)
                except (json.JSONDecodeError, ValueError):
                    pass
        return results

    def search(self, query: str) -> list[AppInfo]:
        resp = self.session.get(f"{self.BASE}/search/{query}", timeout=HTTP_TIMEOUT)
        resp.raise_for_status()

        apps = self._parse_rsc_apps(resp.text)
        return [
            AppInfo(
                package=app.get("packageName", ""),
                name=app.get("title", app.get("packageName", "")),
                version="",
                source=self.name,
            )
            for app in apps
        ]

    def get_info(self, package: str) -> AppInfo | None:
        resp = self.session.get(f"{self.BASE}/apk/{package}", timeout=HTTP_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return self._parse_app_page(package, resp.text)

    def _parse_app_page(self, package: str, html: str) -> AppInfo:
        """Parse app page HTML for metadata + version code."""
        soup = BeautifulSoup(html, "lxml")

        name = package
        version = ""
        size = None

        name_el = soup.select_one("[itemprop='name']")
        if name_el:
            name = name_el.get("content", "") or name_el.get_text(strip=True) or name

        ver_el = soup.select_one("[itemprop='softwareVersion']")
        if ver_el:
            version = ver_el.get("content", "") or ver_el.get_text(strip=True)

        size_el = soup.select_one("[itemprop='fileSize']")
        if size_el:
            size_text = size_el.get("content", "") or size_el.get_text(strip=True)
            size = self._parse_size(size_text)

        return AppInfo(
            package=package,
            name=name,
            version=version,
            size=size,
            source=self.name,
        )

    @staticmethod
    def _parse_size(size_str: str) -> int | None:
        m = re.match(r'([\d.]+)\s*(MB|GB|KB)', size_str, re.IGNORECASE)
        if not m:
            return None
        val = float(m.group(1))
        unit = m.group(2).upper()
        multipliers = {"GB": 1024**3, "MB": 1024**2, "KB": 1024}
        return int(val * multipliers.get(unit, 1))

    def _get_version_map(self, package: str) -> dict[str, str]:
        """Fetch app page and return {version_name: version_code} mapping."""
        resp = self.session.get(f"{self.BASE}/apk/{package}", timeout=HTTP_TIMEOUT)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()

        # Parse version links: /apk/{package}/{version_code}
        # Text: "AppName vX.Y.Z"
        version_map: dict[str, str] = {}
        for m in re.finditer(
            rf'/apk/{re.escape(package)}/(\d+)',
            resp.text,
        ):
            code = m.group(1)
            # Find the version name near this link
            start = max(0, m.start() - 200)
            context = resp.text[start:m.end() + 200]
            ver_m = re.search(r'v([\d]+(?:\.[\d]+)+)', context)
            if ver_m:
                version_map[ver_m.group(1)] = code

        # Also extract from download links
        for m in re.finditer(
            rf'/apk/{re.escape(package)}/download/(\d+)',
            resp.text,
        ):
            code = m.group(1)
            if code not in version_map.values():
                start = max(0, m.start() - 200)
                context = resp.text[start:m.end() + 200]
                ver_m = re.search(r'v([\d]+(?:\.[\d]+)+)', context)
                if ver_m and ver_m.group(1) not in version_map:
                    version_map[ver_m.group(1)] = code

        return version_map

    def list_developer_apps(self, developer: str) -> list[AppInfo]:
        resp = self.session.get(f"{self.BASE}/developer/{developer}", timeout=HTTP_TIMEOUT)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")
        apps: list[AppInfo] = []
        seen: set[str] = set()
        for a in soup.select("a[href*='/apk/']"):
            href = a.get("href", "")
            m = re.search(r'/apk/([\w.]+(?:\.[\w]+){2,})(?:/|$)', href)
            if not m:
                continue
            pkg = m.group(1)
            if pkg in seen:
                continue
            seen.add(pkg)
            name = a.get_text(strip=True)
            if name and "DOWNLOAD" not in name:
                apps.append(AppInfo(
                    package=pkg, name=name, version="", source=self.name,
                ))
        return apps

    def list_versions(self, package: str) -> list[tuple[str, str]]:
        return [(v, "") for v in self._get_version_map(package).keys()]

    def download(
        self, package: str, output_dir: Path, version: str | None = None,
    ) -> DownloadResult:
        # Fetch app page — extract version map
        resp = self.session.get(f"{self.BASE}/apk/{package}", timeout=HTTP_TIMEOUT)
        if resp.status_code == 404:
            raise RuntimeError(f"[apk20] Package not found: {package}")
        resp.raise_for_status()

        if version:
            # Find version code for the requested version
            version_map = self._get_version_map(package)
            version_code = version_map.get(version)
            if not version_code:
                available = list(version_map.keys())[:5]
                raise RuntimeError(
                    f"[apk20] Version {version} not found for {package}. "
                    f"Available: {', '.join(available)}"
                )
            ver = version
        else:
            # Latest: use primary download link
            m = re.search(rf'/apk/{re.escape(package)}/download/(\d+)', resp.text)
            if not m:
                codes = re.findall(r'"versionCode":\s*(\d+)', resp.text)
                if not codes:
                    raise RuntimeError(f"[apk20] No versions found for: {package}")
                version_code = codes[0]
            else:
                version_code = m.group(1)

            info = self._parse_app_page(package, resp.text)
            ver = info.version or "unknown"

        # Verify + get download URL
        verify_resp = self.session.get(
            f"{self.BASE}/api/verify/{package}/{version_code}",
            timeout=HTTP_TIMEOUT,
        )
        verify_resp.raise_for_status()
        verify_data = verify_resp.json()

        if not verify_data.get("success"):
            raise RuntimeError(
                f"[apk20] Verify failed: {verify_data.get('message', 'unknown error')}"
            )

        filename = verify_data["filename"]
        dl_url = f"{self.FILE_SERVER}/{filename}"

        out_filename = f"{package}-{ver}{_ext(filename)}"
        out_path = output_dir / out_filename

        log_source(self.name, f"Downloading {package} v{ver}")
        size = download_file(dl_url, out_path, self.session)

        return DownloadResult(
            path=out_path,
            package=package,
            version=ver,
            source=self.name,
            size=size,
            sha256=sha256_file(out_path),
        )


def _ext(filename: str) -> str:
    """Extract file extension from filename."""
    if "." in filename:
        return "." + filename.rsplit(".", 1)[-1]
    return ".apk"
