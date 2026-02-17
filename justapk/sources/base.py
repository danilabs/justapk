from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from justapk.models import AppInfo, DownloadResult


class APKSource(ABC):
    name: str

    @abstractmethod
    def search(self, query: str) -> list[AppInfo]:
        ...

    @abstractmethod
    def get_info(self, package: str) -> AppInfo | None:
        ...

    @abstractmethod
    def download(
        self,
        package: str,
        output_dir: Path,
        version: str | None = None,
    ) -> DownloadResult:
        ...

    def list_versions(self, package: str) -> list[tuple[str, str]]:
        """Return available versions as (version_name, date_str) tuples.

        *date_str* is ``YYYY-MM-DD`` when available, otherwise ``""``.
        Default implementation returns the latest version only.
        Sources with version history should override this.
        """
        info = self.get_info(package)
        if info and info.version:
            return [(info.version, "")]
        return []

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} ({self.name})>"
