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

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} ({self.name})>"
