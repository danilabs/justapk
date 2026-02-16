"""justapk — Multi-source APK downloader."""

__version__ = "0.1.1"

from justapk.downloader import APKDownloader
from justapk.models import AppInfo, DownloadResult

__all__ = ["APKDownloader", "AppInfo", "DownloadResult", "__version__"]
