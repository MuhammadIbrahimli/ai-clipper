import json
import logging
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clipper.core.config import Campaign, TargetAccount
from clipper.publish.base import Adapter, ClipInfo, PublishError

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_STEP_MIN = 30  # pause before retry: 30 min, then 60 min
INTERRUPTED = "прервано перезапуском во время загрузки, проверьте канал вручную"
FMT = "%Y-%m-%dT%H:%M:%S"


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse(s: str) -> datetime:
    return datetime.strptime(s, FMT)


def _account_filter(t: TargetAccount) -> tuple[str, tuple[str, str]]:
    return "platform = ? AND account_label = ?", (t.platform, t.account_label)


def enqueue(conn: sqlite3.Connection, campaign: Campaign, now: datetime, rng: random.Random) -> int:
    clips = [r[0] for r in conn.execute(
        "SELECT id FROM clips WHERE campaign = ? AND status = 'approved' ORDER BY id", (campaign.id,))]
    gap = campaign.limits.min_gap_minutes
    added = 0
    for t in campaign.target_accounts:
        where, args = _account_filter(t)
        last_row = conn.execute(
            f"SELECT MAX(scheduled_at) FROM publications WHERE campaign = ? AND {where}", (campaign.id, *args)
        ).fetchone()[0]
        last = _parse(last_row) if last_row else None
        for clip_id in clips:
            if conn.execute(f"SELECT 1 FROM publications WHERE clip_id = ? AND {where}", (clip_id, *args)).fetchone():
                continue
            at = now if last is None else max(now, last + timedelta(minutes=gap + rng.uniform(0, gap)))
            with conn:
                conn.execute(
                    "INSERT INTO publications (campaign, clip_id, platform, account_label, status, attempts, scheduled_at)"
                    " VALUES (?, ?, ?, ?, 'queued', 0, ?)",
                    (campaign.id, clip_id, t.platform, t.account_label, at.strftime(FMT)),
                )
            last = at
            added += 1
    return added


def _clip(conn: sqlite3.Connection, clip_id: int) -> ClipInfo:
    row = conn.execute(
        "SELECT id, render_path, title_overlay, description, hashtags, mentions FROM clips WHERE id = ?", (clip_id,)
    ).fetchone()
    return ClipInfo(row[0], Path(row[1]), row[2], row[3], json.loads(row[4]), json.loads(row[5]))


def run_due(conn: sqlite3.Connection, campaign: Campaign, adapters: dict[str, Adapter], now: datetime) -> list[str]:
    # A row still 'uploading' means the process died mid-upload: the video may already be online, so don't re-upload.
    with conn:
        conn.execute("UPDATE publications SET status = 'failed', last_error = ?"
                     " WHERE campaign = ? AND status = 'uploading'", (INTERRUPTED, campaign.id))
    msgs = []
    gap = timedelta(minutes=campaign.limits.min_gap_minutes)
    for t in campaign.target_accounts:
        name = f"{t.platform}/{t.account_label}"
        adapter = adapters.get(t.platform)
        if adapter is None:
            msgs.append(f"{name}: адаптер платформы не реализован, пропуск")
            continue
        where, args = _account_filter(t)
        today = now.strftime("%Y-%m-%d")
        published_today = conn.execute(
            f"SELECT COUNT(*) FROM publications WHERE campaign = ? AND {where} AND status = 'published'"
            " AND published_at LIKE ?", (campaign.id, *args, f"{today}%")).fetchone()[0]
        if published_today >= campaign.limits.per_day:
            msgs.append(f"{name}: дневной лимит {campaign.limits.per_day} исчерпан")
            continue
        last_pub = conn.execute(
            f"SELECT MAX(published_at) FROM publications WHERE campaign = ? AND {where} AND status = 'published'",
            (campaign.id, *args)).fetchone()[0]
        if last_pub and now - _parse(last_pub) < gap:
            msgs.append(f"{name}: пауза min_gap_minutes ещё не прошла")
            continue
        row = conn.execute(
            f"SELECT id, clip_id, attempts FROM publications WHERE campaign = ? AND {where} AND status = 'queued'"
            " AND scheduled_at <= ? ORDER BY scheduled_at, id LIMIT 1",
            (campaign.id, *args, now.strftime(FMT))).fetchone()
        if row is None:
            continue
        pid, clip_id, attempts = row
        attempts += 1
        with conn:
            conn.execute("UPDATE publications SET status = 'uploading', attempts = ? WHERE id = ?", (attempts, pid))
        try:
            external_id = adapter.publish(_clip(conn, clip_id), t)
        except PublishError as e:
            if attempts >= MAX_ATTEMPTS:
                status, at = "failed", None
            else:
                status = "queued"
                at = (now + timedelta(minutes=RETRY_STEP_MIN * attempts)).strftime(FMT)
            with conn:
                conn.execute(
                    "UPDATE publications SET status = ?, scheduled_at = COALESCE(?, scheduled_at), last_error = ?"
                    " WHERE id = ?", (status, at, str(e), pid))
            msgs.append(f"клип {clip_id} → {name}: ошибка, попытка {attempts}/{MAX_ATTEMPTS}, статус {status}: {e}")
            log.warning("publish failed: %s", e)
            continue
        with conn:
            conn.execute(
                "UPDATE publications SET status = 'published', external_id = ?, published_at = ? WHERE id = ?",
                (external_id, now.strftime(FMT), pid))
        msgs.append(f"клип {clip_id} → {name}: опубликовано, id {external_id}")
    return msgs
