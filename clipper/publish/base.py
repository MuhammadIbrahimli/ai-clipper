from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from clipper.core.config import TargetAccount


class PublishError(Exception):
    pass


@dataclass(frozen=True)
class ClipInfo:
    id: int
    path: Path
    title: str
    description: str
    hashtags: list[str]
    mentions: list[str]


class Adapter(Protocol):
    platform: str

    def publish(self, clip: ClipInfo, account: TargetAccount) -> str:
        """Uploads the clip and returns the platform's id for it. Raises PublishError on failure."""
