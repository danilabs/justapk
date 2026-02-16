from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class AppInfo:
    package: str
    name: str
    version: str
    version_code: int | None = None
    size: int | None = None
    source: str = ""
    icon_url: str | None = None
    description: str | None = None

    def to_dict(self) -> dict:
        d: dict = {
            "package": self.package,
            "name": self.name,
            "version": self.version,
            "source": self.source,
        }
        if self.version_code is not None:
            d["version_code"] = self.version_code
        if self.size is not None:
            d["size"] = self.size
        if self.description:
            d["description"] = self.description
        if self.icon_url:
            d["icon_url"] = self.icon_url
        return d


@dataclass
class DownloadResult:
    path: Path
    package: str
    version: str
    source: str
    size: int
    sha256: str

    def to_dict(self) -> dict:
        return {
            "path": str(self.path),
            "package": self.package,
            "version": self.version,
            "source": self.source,
            "size": self.size,
            "sha256": self.sha256,
        }
