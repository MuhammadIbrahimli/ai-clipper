"""Pipeline steps shared by the CLI and the web worker. Each step reports progress through `say`."""
import logging
import os
import random
import sqlite3
from datetime import date
from pathlib import Path
from typing import Callable

from clipper.core.config import Campaign, Settings
from clipper.ingest.scan import ingest_campaign
from clipper.publish.android import AndroidAdapter
from clipper.publish.base import Adapter
from clipper.publish.schedule import FMT, enqueue, run_due, utc_now
from clipper.publish.youtube import YouTubeAdapter
from clipper.render.gate import check_clip
from clipper.render.render import RenderError, render_clip
from clipper.select.select import select_source
from clipper.transcribe.transcribe import transcribe

log = logging.getLogger(__name__)
Say = Callable[[str], None]


def apply_tool_paths(settings: Settings) -> None:
    """ffmpeg/ffprobe are called by name; a configured full path just puts its folder first on PATH."""
    p = Path(settings.ffmpeg_path)
    if p.parent != Path("."):
        os.environ["PATH"] = str(p.parent.resolve()) + os.pathsep + os.environ.get("PATH", "")


def build_adapters(settings: Settings) -> dict[str, Adapter]:
    adapters: dict[str, Adapter] = {p: AndroidAdapter(p, app, settings.adb_path) for p, app in settings.android_apps.items()}
    adapters["youtube"] = YouTubeAdapter()  # the official API wins over an Android route for the same platform
    return adapters


def ingest_step(campaign: Campaign, conn: sqlite3.Connection, say: Say) -> None:
    results = ingest_campaign(campaign, conn)
    new = sum(r.new for r in results)
    say(f"ingest: файлов {len(results)}, новых {new}, уже были {len(results) - new}")


def select_step(campaign: Campaign, settings: Settings, conn: sqlite3.Connection, say: Say) -> None:
    rows = conn.execute(
        "SELECT sha256, path FROM sources WHERE campaign = ? AND status = 'registered'", (campaign.id,)
    ).fetchall()
    if not rows:
        say("нет зарегистрированных исходников, сначала выполните ingest")
        return
    for sha256, path in rows:
        res = select_source(campaign, sha256, Path(path), settings, conn)
        say(f"{path}: нет клипов, причина: {res.reason}" if res.reason else f"{path}: новых клипов {res.inserted}")


def render_step(campaign: Campaign, settings: Settings, conn: sqlite3.Connection, say: Say,
                clip_ids: list[int] | None = None) -> None:
    clips = conn.execute(
        "SELECT c.id, c.start, c.end, c.hook_start, c.hook_end, c.subtitle_preset, s.path FROM clips c"
        " JOIN sources s ON s.sha256 = c.source_sha256 AND s.campaign = c.campaign"
        " WHERE c.campaign = ? AND c.status = 'selected' ORDER BY c.id",
        (campaign.id,),
    ).fetchall()
    if clip_ids is not None:
        clips = [c for c in clips if c[0] in clip_ids]
    if not clips:
        say("нет отобранных клипов для рендера")
        return
    for clip_id, start, end, hook_start, hook_end, preset, path in clips:
        words = [w for s in transcribe(Path(path), settings.whisper_model)["segments"] for w in s["words"]]
        out = Path(campaign.output_dir) / campaign.id / date.today().isoformat() / f"{clip_id}.mp4"
        clip_campaign = campaign.model_copy(update={"subtitle_preset": preset or campaign.subtitle_preset})
        try:
            res = render_clip(clip_campaign, words, Path(path), start, end, hook_start, hook_end, out,
                              Path("data/render_tmp") / str(clip_id))
        except RenderError as e:
            log.error("клип %s: рендер не выполнен: %s", clip_id, e)
            say(f"клип {clip_id}: рендер не выполнен: {e}")
            continue
        with conn:
            conn.execute(
                "UPDATE clips SET status = 'rendered', render_path = ?, subtitle_preset = ?, speed_factor = ?,"
                " has_teaser = 1, has_callouts = ?, zoom_enabled = ?, duration_s = ? WHERE id = ?",
                (str(res.path), clip_campaign.subtitle_preset, res.speed_factor, int(res.has_callouts),
                 int(res.zoom_enabled), res.duration_s, clip_id),
            )
        say(f"клип {clip_id}: {res.path}, {res.duration_s:.1f} с, рендер {res.took_s:.1f} с")


def gate_step(campaign: Campaign, conn: sqlite3.Connection, say: Say) -> None:
    rows = conn.execute(
        "SELECT id, render_path, hook_start, hook_end, has_teaser, description FROM clips"
        " WHERE campaign = ? AND status = 'rendered' ORDER BY id",
        (campaign.id,),
    ).fetchall()
    for clip_id, render_path, hook_start, hook_end, has_teaser, description in rows:
        reasons = check_clip(campaign, Path(render_path), hook_start, hook_end, bool(has_teaser), description)
        status, reason = ("rejected", "; ".join(reasons)) if reasons else ("ready", None)
        with conn:
            conn.execute("UPDATE clips SET status = ?, reject_reason = ? WHERE id = ?", (status, reason, clip_id))
        say(f"клип {clip_id}: {status}" + (f" — {reason}" if reason else ""))


def publish_step(campaign: Campaign, settings: Settings, conn: sqlite3.Connection, say: Say,
                 now_first: bool = False, adapters: dict[str, Adapter] | None = None) -> None:
    """now_first moves each account's earliest queued item to now; per_day and min_gap still apply."""
    utc = utc_now()
    say(f"в очередь добавлено {enqueue(conn, campaign, utc, random.Random())}")
    if now_first:
        with conn:
            for t in campaign.target_accounts:
                conn.execute(
                    "UPDATE publications SET scheduled_at = ? WHERE id = (SELECT id FROM publications"
                    " WHERE campaign = ? AND platform = ? AND account_label = ? AND status = 'queued'"
                    " ORDER BY scheduled_at, id LIMIT 1)",
                    (utc.strftime(FMT), campaign.id, t.platform, t.account_label))
    for msg in run_due(conn, campaign, adapters or build_adapters(settings), utc):
        say(msg)
