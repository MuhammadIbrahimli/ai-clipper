import re
import subprocess
from pathlib import Path

from clipper.core.config import Campaign
from clipper.ingest.scan import IngestError, probe

BLACK_MIN_S = 0.5
BLACK_RE = re.compile(r"black_start:(\d+\.?\d*) black_end:(\d+\.?\d*)")


def black_runs(path: Path) -> list[tuple[float, float]]:
    proc = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(path), "-vf", f"blackdetect=d={BLACK_MIN_S}", "-an", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", check=False,
    )
    return [(float(a), float(b)) for a, b in BLACK_RE.findall(proc.stderr)]


def check_clip(campaign: Campaign, path: Path, hook_start: float, hook_end: float,
               has_teaser: bool, description: str) -> list[str]:
    """Returns the list of failed checks; an empty list means the clip may become ready."""
    try:
        info = probe(path)
    except IngestError as e:
        return [f"файл не читается: {e}"]
    reasons = []
    if not campaign.clip.min_s <= info.duration_s <= campaign.clip.max_s:
        reasons.append(f"длительность {info.duration_s:.1f} с вне {campaign.clip.min_s:g}-{campaign.clip.max_s:g}")
    if not info.has_audio:
        reasons.append("нет аудио")
    for a, b in black_runs(path):
        reasons.append(f"чёрный кадр {b - a:.1f} с на {a:.1f} с")
    if not (has_teaser and 2 <= hook_end - hook_start <= 4):
        reasons.append("тизер отсутствует или не 2-4 с")
    text = description.lower()
    for tag in campaign.required_hashtags:
        if tag.lower() not in text:
            reasons.append(f"нет тега из ТЗ в описании: {tag}")
    for mention in campaign.required_mentions:
        if mention.lower() not in text:
            reasons.append(f"нет упоминания из ТЗ в описании: {mention}")
    return reasons
