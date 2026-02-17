from __future__ import annotations

import hashlib
import json
import random
import re
import time
import uuid
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from justapk.models import AppInfo, DownloadResult
from justapk.sources.base import APKSource
from justapk.utils import HTTP_TIMEOUT, create_cf_session, download_file, log_source, sha256_file

_WEB_BASE = "https://apkpure.com"

# Reverse-engineered from APKPure Android app v3.20.6309
_API_BASE = "https://tapi.pureapk.com/v3"
_AUTH_KEY = "qNKrYmW8SSUqJ73k3P2yfMxRTo3sJTR"
_SIGN_SECRET = "d33cb23fd17fda8ea38be504929b77ef"


def _make_headers() -> dict[str, str]:
    """Build headers matching the APKPure mobile app."""
    device_uuid = str(uuid.uuid4())
    project_a = json.dumps({
        "device_info": {
            "abis": ["arm64-v8a", "armeabi-v7a"],
            "android_id": hashlib.md5(device_uuid.encode()).hexdigest()[:16],
            "brand": "samsung", "country": "United States",
            "country_code": "US", "imei": "", "language": "en-US",
            "manufacturer": "samsung", "mode": "SM-G955F",
            "os_ver": "34", "os_ver_name": "14", "platform": 1,
            "product": "dream2lte", "screen_height": 2888, "screen_width": 1440,
        },
        "host_app_info": {
            "build_no": "873", "channel": "", "md5": "",
            "pkg_name": "com.apkpure.aegon", "sdk_ver": "3.20.6309",
            "version_code": 3206397, "version_name": "3.20.6309",
        },
        "net_info": {
            "carrier_code": 0, "ipv4": "", "ipv6": "", "mac_address": "",
            "net_type": 1, "use_vpn": False, "wifi_bssid": "", "wifi_ssid": "",
        },
        "user_info": {
            "auth_key": _AUTH_KEY, "country": "United States",
            "country_code": "US", "guid": "", "language": "en-US",
            "qimei": "", "qimei_token": "", "user_id": "",
            "uuid": device_uuid,
        },
    }, separators=(",", ":"))

    ext_info = json.dumps({
        "ext_info": '{"gaid":"","oaid":""}',
        "lbs_info": {
            "accuracy": 0.0, "city": "", "city_code": 0,
            "country": "", "country_code": "", "district": "",
            "latitude": 0.0, "longitude": 0.0, "province": "", "street": "",
        },
    }, separators=(",", ":"))

    return {
        "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; SM-G955F "
                       "Build/AP2A.240805.005); APKPure/3.20.6309 (Aegon)",
        "Ual-Access-Businessid": "projecta",
        "Ual-Access-ProjectA": project_a,
        "Ual-Access-ExtInfo": ext_info,
        "Ual-Access-Sequence": str(uuid.uuid4()),
        "Ual-Access-Signature": "",
        "Ual-Access-Nonce": "0",
        "Ual-Access-Timestamp": "0",
        "Accept-Encoding": "gzip",
    }


def _sign_body(headers: dict[str, str], body: str) -> None:
    """Compute MD5 signature for POST requests."""
    ts = str(int(time.time() * 1000))
    nonce = str(random.randint(10000000, 99999999))
    sig = hashlib.md5((body + ts + _SIGN_SECRET + nonce).encode()).hexdigest()
    headers["Ual-Access-Signature"] = sig
    headers["Ual-Access-Nonce"] = nonce
    headers["Ual-Access-Timestamp"] = ts
    headers["Content-Type"] = "application/json; charset=utf-8"


class APKPureSource(APKSource):
    name = "apkpure"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(_make_headers())
        self._web_session = None

    def _get_web_session(self):
        """Lazy-init curl_cffi session for web scraping (Cloudflare bypass)."""
        if self._web_session is None:
            self._web_session = create_cf_session()
        return self._web_session

    def _find_slug(self, package: str) -> str | None:
        """Resolve package name to APKPure URL slug via redirect."""
        session = self._get_web_session()
        resp = session.get(
            f"{_WEB_BASE}/r/{package}/versions",
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return None
        # URL becomes: https://apkpure.com/{slug}/{package}/versions
        m = re.search(rf"apkpure\.com/([^/]+)/{re.escape(package)}", str(resp.url))
        return m.group(1) if m else None

    def search(self, query: str) -> list[AppInfo]:
        resp = self.session.get(
            f"{_API_BASE}/search_query_new",
            params={"hl": "en-US", "key": query, "page": "1",
                    "search_type": "active_search"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        seen = set()
        for section in data.get("data", {}).get("data", []):
            for item in section.get("data", []):
                ai = item.get("app_info", {})
                pkg = ai.get("package_name", "")
                if not pkg or pkg in seen:
                    continue
                seen.add(pkg)
                results.append(AppInfo(
                    package=pkg,
                    name=ai.get("title", pkg),
                    version=ai.get("version_name", ""),
                    source=self.name,
                    description=ai.get("description_short", ""),
                ))
        return results

    def get_info(self, package: str) -> AppInfo | None:
        detail = self._get_detail(package)
        if not detail:
            return None
        return AppInfo(
            package=package,
            name=detail.get("title", package),
            version=detail.get("version_name", ""),
            source=self.name,
            description=detail.get("description_short", ""),
        )

    def list_developer_apps(self, developer: str) -> list[AppInfo]:
        from urllib.parse import quote

        session = self._get_web_session()
        apps: list[AppInfo] = []
        seen: set[str] = set()
        page = 1

        while True:
            url = f"{_WEB_BASE}/en/developer/{quote(developer)}"
            if page > 1:
                url += f"?page={page}"
            resp = session.get(url, timeout=HTTP_TIMEOUT)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "lxml")

            found = 0
            for a in soup.select("a[href]"):
                href = a.get("href", "").strip("/")
                parts = href.split("/")
                if len(parts) >= 2 and "." in parts[-1]:
                    pkg = parts[-1]
                    if pkg in seen:
                        continue
                    seen.add(pkg)
                    name_el = a.select_one(".p1, .app-title, span")
                    name = name_el.get_text(strip=True) if name_el else a.get_text(strip=True)
                    if name and pkg:
                        apps.append(AppInfo(
                            package=pkg, name=name, version="", source=self.name,
                        ))
                        found += 1

            if found == 0:
                break
            next_link = soup.select_one("a.nextpostslink[href], a[rel='next'][href]")
            if not next_link:
                break
            page += 1

        return apps

    def list_versions(self, package: str) -> list[tuple[str, str]]:
        slug = self._find_slug(package)
        if not slug:
            return []
        session = self._get_web_session()
        resp = session.get(
            f"{_WEB_BASE}/{slug}/{package}/versions",
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        versions: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in soup.select(".ver-item"):
            name_el = item.select_one(".ver-item-n")
            if not name_el:
                continue
            m = re.search(r"([\d]+\.[\d]+[\d.]*)", name_el.get_text())
            if m:
                v = m.group(1)
                if v not in seen:
                    seen.add(v)
                    date_el = item.select_one(".ver-item-d")
                    date_str = date_el.get_text(strip=True) if date_el else ""
                    versions.append((v, date_str))
        return versions

    def _get_detail(self, package: str) -> dict | None:
        """Call get_app_detail API."""
        body = json.dumps({"package_name": package, "hl": "en-US"})
        headers = dict(self.session.headers)
        _sign_body(headers, body)
        resp = self.session.post(
            f"{_API_BASE}/get_app_detail",
            data=body, headers=headers, timeout=HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("app_detail")

    def download(self, package: str, output_dir: Path, version: str | None = None) -> DownloadResult:
        # For a specific version, try web download (supports old versions)
        if version:
            return self._download_web(package, output_dir, version)

        # Latest version: use mobile API (faster, no Cloudflare)
        detail = self._get_detail(package)
        if not detail:
            raise RuntimeError(f"[apkpure] Package not found: {package}")

        asset = detail.get("asset", {})
        dl_url = asset.get("url", "")
        if not dl_url:
            raise RuntimeError(f"[apkpure] No download URL for: {package}")

        ver = detail.get("version_name", "") or "latest"
        file_type = asset.get("type", "APK").lower()
        filename = f"{package}-{ver}.{file_type}"
        out_path = output_dir / filename

        log_source(self.name, f"Downloading {package} v{ver} ({file_type})")
        size = download_file(dl_url, out_path, self.session)

        return DownloadResult(
            path=out_path,
            package=package,
            version=ver,
            source=self.name,
            size=size,
            sha256=sha256_file(out_path),
        )

    def _download_web(self, package: str, output_dir: Path, version: str) -> DownloadResult:
        """Download a specific version via web scraping (supports old versions)."""
        slug = self._find_slug(package)
        if not slug:
            raise RuntimeError(f"[apkpure] Package not found: {package}")

        session = self._get_web_session()
        resp = session.get(
            f"{_WEB_BASE}/{slug}/{package}/download/{version}",
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"[apkpure] Version page not found: {package} v{version}")

        soup = BeautifulSoup(resp.text, "lxml")

        # Primary: <a id="download_link">
        dl_link = soup.select_one("a#download_link[href]")
        if not dl_link:
            # Fallback: .download-start-btn
            dl_link = soup.select_one("a.download-start-btn[href]")
        if not dl_link:
            raise RuntimeError(f"[apkpure] No download link for: {package} v{version}")

        dl_url = dl_link["href"]
        # Detect file type from button text
        btn_text = dl_link.get_text(strip=True).lower()
        file_type = "xapk" if "xapk" in btn_text else "apk"

        filename = f"{package}-{version}.{file_type}"
        out_path = output_dir / filename

        log_source(self.name, f"Downloading {package} v{version} ({file_type})")
        size = download_file(dl_url, out_path, session)

        return DownloadResult(
            path=out_path,
            package=package,
            version=version,
            source=self.name,
            size=size,
            sha256=sha256_file(out_path),
        )
