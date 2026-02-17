from __future__ import annotations

import hashlib
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HTTP_TIMEOUT = 30

# ── Terminal symbols (ASCII-safe fallback) ──────────────────────────────────
_UTF8 = (
    hasattr(sys.stderr, "encoding")
    and sys.stderr.encoding
    and "utf" in sys.stderr.encoding.lower()
)
_SYM_OK = "\u2714" if _UTF8 else "+"
_SYM_FAIL = "\u2718" if _UTF8 else "x"
_SYM_WARN = "\u26a0" if _UTF8 else "!"
_SYM_DOT = "\u2022" if _UTF8 else "*"
_SYM_DL = "\u2193" if _UTF8 else "v"
_SYM_BAR = "\u2588" if _UTF8 else "#"
_SYM_BAR_BG = "\u2591" if _UTF8 else "."
_SYM_ARROW = "\u279c" if _UTF8 else ">"


def _term_width() -> int:
    return shutil.get_terminal_size((80, 24)).columns


# ── Logging helpers ─────────────────────────────────────────────────────────

def log_step(current: int, total: int, msg: str) -> None:
    """Log a numbered step: [1/6] Trying apk20..."""
    sys.stderr.write(f"  [{current}/{total}] {msg}\n")


def log_ok(msg: str) -> None:
    sys.stderr.write(f"  {_SYM_OK} {msg}\n")


def log_fail(msg: str) -> None:
    sys.stderr.write(f"  {_SYM_FAIL} {msg}\n")


def log_warn(msg: str) -> None:
    sys.stderr.write(f"  {_SYM_WARN} {msg}\n")


def log_info(msg: str) -> None:
    sys.stderr.write(f"  {_SYM_DOT} {msg}\n")


def log_source(source: str, msg: str) -> None:
    """Log a message with source prefix."""
    sys.stderr.write(f"  [{source}] {msg}\n")


def log_header(msg: str) -> None:
    """Log a section header."""
    sys.stderr.write(f"\n  {_SYM_ARROW} {msg}\n")


# ── Size formatting ─────────────────────────────────────────────────────────

def format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 ** 3):.1f} GB"
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"


def format_elapsed(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


# ── HTTP sessions ───────────────────────────────────────────────────────────

def create_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
    })
    return s


def create_cf_session() -> Any:
    from curl_cffi.requests import Session
    return Session(impersonate="chrome131")


# ── File download ───────────────────────────────────────────────────────────

def download_file(
    url: str,
    path: Path,
    session: Any = None,
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
    t0 = time.monotonic()

    try:
        with open(part_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size):
                f.write(chunk)
                downloaded += len(chunk)
                _print_progress(downloaded, total, t0)

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


def _print_progress(downloaded: int, total: int, t0: float) -> None:
    elapsed = time.monotonic() - t0
    speed = downloaded / elapsed if elapsed > 0.1 else 0

    if total > 0:
        pct = downloaded * 100 // total
        bar_width = min(25, _term_width() - 55)
        if bar_width > 5:
            filled = bar_width * downloaded // total
            bar = _SYM_BAR * filled + _SYM_BAR_BG * (bar_width - filled)
            line = f"\r  {_SYM_DL} {bar} {pct:3d}%  {format_size(downloaded)}/{format_size(total)}"
        else:
            line = f"\r  {_SYM_DL} {pct:3d}%  {format_size(downloaded)}/{format_size(total)}"
        if speed > 0:
            line += f"  {format_size(int(speed))}/s"
    else:
        line = f"\r  {_SYM_DL} {format_size(downloaded)}"
        if speed > 0:
            line += f"  {format_size(int(speed))}/s"

    # Pad to clear previous line remnants
    width = _term_width()
    if len(line) < width:
        line += " " * (width - len(line))

    sys.stderr.write(line)
    sys.stderr.flush()


# ── Hashing ─────────────────────────────────────────────────────────────────

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 64), b""):
            h.update(chunk)
    return h.hexdigest()


def sanitize_filename(name: str) -> str:
    """Remove characters unsafe for filenames."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
