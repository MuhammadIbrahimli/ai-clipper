import hashlib
import json
import logging
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from clipper.core.config import Campaign

log = logging.getLogger(__name__)

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


class IngestError(Exception):
    pass


@dataclass(frozen=True)
class Probe:
    duration_s: float
    fps: float
    width: int
    height: int
    has_audio: bool


@dataclass(frozen=True)
class Registered:
    path: Path
    sha256: str
    new: bool


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def probe(path: Path) -> Probe:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )
    except FileNotFoundError as e:
        raise IngestError("ffprobe не найден в PATH") from e
    if proc.returncode != 0:
        raise IngestError(f"ffprobe не смог прочитать {path}: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise IngestError(f"{path}: нет видеодорожки")
    num, den = video["r_frame_rate"].split("/")
    return Probe(
        duration_s=float(data["format"].get("duration") or 0),
        fps=float(num) / float(den) if float(den) else 0.0,
        width=int(video["width"]),
        height=int(video["height"]),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def collect_files(sources: list[Path]) -> list[Path]:
    files: list[Path] = []
    for src in sources:
        if not src.exists():
            raise IngestError(f"источник не найден: {src}")
        if src.is_file():
            files.append(src)
        else:
            files.extend(sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS))
    return files


def ingest_campaign(campaign: Campaign, conn: sqlite3.Connection) -> list[Registered]:
    results: list[Registered] = []
    for path in collect_files(campaign.sources):
        try:
            info = probe(path)
        except IngestError as e:
            log.warning("пропуск: %s", e)
            continue
        digest = sha256_file(path)
        with conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO sources (campaign, sha256, path, duration_s, fps, width, height, has_audio, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (campaign.id, digest, str(path), info.duration_s, info.fps,
                 info.width, info.height, info.has_audio, "registered"),
            )
        new = cur.rowcount == 1
        log.info("%s %s", "зарегистрирован" if new else "уже в базе", path)
        results.append(Registered(path=path, sha256=digest, new=new))
    return results
