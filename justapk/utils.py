from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HTTP_TIMEOUT = 30


def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def create_cf_session():
    from curl_cffi.requests import Session
    return Session(impersonate="chrome131")


def download_file(
    url: str,
    path: Path,
    session=None,
    headers: dict | None = None,
    chunk_size: int = 1024 * 64,
) -> int:
    """Download a file with progress bar on stderr. Returns total bytes."""
    if session is None:
        session = create_session()

    resp = session.get(url, headers=headers or {}, stream=True, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    total = int(resp.headers.get("content-length", 0))

    downloaded = 0
    part_path = path.with_suffix(path.suffix + ".part")
    part_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(part_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size):
                f.write(chunk)
                downloaded += len(chunk)
                _print_progress(downloaded, total)

        sys.stderr.write("\n")

        if total > 0 and downloaded < total:
            part_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"Incomplete download: {downloaded}/{total} bytes"
            )

        part_path.rename(path)
    except BaseException:
        part_path.unlink(missing_ok=True)
        raise

    return downloaded


def _print_progress(downloaded: int, total: int) -> None:
    if total > 0:
        pct = downloaded * 100 // total
        mb_dl = downloaded / (1024 * 1024)
        mb_total = total / (1024 * 1024)
        sys.stderr.write(f"\r  {mb_dl:.1f}/{mb_total:.1f} MB ({pct}%)")
    else:
        mb_dl = downloaded / (1024 * 1024)
        sys.stderr.write(f"\r  {mb_dl:.1f} MB")
    sys.stderr.flush()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 64), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filenames."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
