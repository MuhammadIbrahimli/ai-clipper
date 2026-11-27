import random
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from clipper.core.config import Campaign
from clipper.core.db import connect
from clipper.publish.base import PublishError
from clipper.publish.schedule import FMT, enqueue, run_due

NOW = datetime(2026, 11, 22, 10, 0, 0)


class MockAdapter:
    platform = "youtube"

    def __init__(self, fail: int = 0):
        self.fail = fail
        self.calls: list[int] = []

    def publish(self, clip, account):
        self.calls.append(clip.id)
        if self.fail:
            self.fail -= 1
            raise PublishError("mock failure")
        return f"yt-{clip.id}"


def campaign(per_day=2, gap=60, platform="youtube"):
    return Campaign(id="demo", name="Demo", mode="clip_source", sources=["x.mp4"],
                    target_accounts=[{"platform": platform, "account_label": "main"}],
                    limits={"per_day": per_day, "min_gap_minutes": gap})


def add_clips(conn, n):
    for i in range(n):
        conn.execute(
            "INSERT INTO clips (campaign, source_sha256, start, end, hook_start, hook_end, hook_text, hook_type,"
            " score, reason, title_overlay, description, hashtags, mentions, status, render_path)"
            " VALUES ('demo', 'abc', ?, ?, 0, 3, 'hook', 'conflict', 80, 'r', 'Title', 'Desc #clip', '[\"#clip\"]',"
            " '[]', 'approved', ?)",
            (i * 100.0, i * 100.0 + 40, f"out/{i}.mp4"),
        )
    conn.commit()


@pytest.fixture
def db(tmp_path):
    conn = connect(tmp_path / "clipper.db")
    add_clips(conn, 3)
    return tmp_path / "clipper.db", conn


def statuses(conn):
    return conn.execute("SELECT clip_id, status FROM publications ORDER BY clip_id").fetchall()


def test_enqueue_is_idempotent(db):
    _, conn = db
    assert enqueue(conn, campaign(), NOW, random.Random(1)) == 3
    assert enqueue(conn, campaign(), NOW, random.Random(1)) == 0
    assert conn.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 3


def test_schedule_spacing_is_at_least_min_gap(db):
    _, conn = db
    enqueue(conn, campaign(gap=60), NOW, random.Random(7))
    times = [datetime.strptime(r[0], FMT) for r in conn.execute("SELECT scheduled_at FROM publications ORDER BY id")]
    assert times[0] == NOW
    for a, b in zip(times, times[1:]):
        assert b - a >= timedelta(minutes=60)


def test_daily_limit_then_next_day(db):
    _, conn = db
    c = campaign(per_day=1)
    adapter = MockAdapter()
    enqueue(conn, c, NOW, random.Random(3))
    assert "опубликовано" in run_due(conn, c, {"youtube": adapter}, NOW)[0]
    later_same_day = run_due(conn, c, {"youtube": adapter}, NOW + timedelta(hours=2))
    assert "дневной лимит" in later_same_day[0]
    assert len(adapter.calls) == 1
    run_due(conn, c, {"youtube": adapter}, NOW + timedelta(days=1))
    assert len(adapter.calls) == 2


def test_restart_does_not_duplicate(db):
    path, conn = db
    c = campaign()
    adapter = MockAdapter()
    enqueue(conn, c, NOW, random.Random(3))
    run_due(conn, c, {"youtube": adapter}, NOW)
    conn.close()

    fresh = connect(path)  # simulated restart: new process, same SQLite file
    assert enqueue(fresh, c, NOW, random.Random(3)) == 0
    run_due(fresh, c, {"youtube": adapter}, NOW + timedelta(days=1))
    run_due(fresh, c, {"youtube": adapter}, NOW + timedelta(days=2))
    assert sorted(adapter.calls) == [1, 2, 3]  # each clip published once


def test_crash_mid_upload_is_not_reuploaded(db):
    _, conn = db
    c = campaign()
    adapter = MockAdapter()
    enqueue(conn, c, NOW, random.Random(3))
    conn.execute("UPDATE publications SET status = 'uploading' WHERE clip_id = 1")
    conn.commit()
    run_due(conn, c, {"youtube": adapter}, NOW)
    assert 1 not in adapter.calls
    row = conn.execute("SELECT status, last_error FROM publications WHERE clip_id = 1").fetchone()
    assert row[0] == "failed" and "проверьте" in row[1]


def test_retry_after_error_then_success(db):
    _, conn = db
    c = campaign(per_day=5)
    adapter = MockAdapter(fail=1)
    enqueue(conn, c, NOW, random.Random(3))
    msgs = run_due(conn, c, {"youtube": adapter}, NOW)
    assert "попытка 1/3" in msgs[0]
    row = conn.execute("SELECT status, attempts, scheduled_at FROM publications WHERE clip_id = 1").fetchone()
    assert row == ("queued", 1, (NOW + timedelta(minutes=30)).strftime(FMT))
    run_due(conn, c, {"youtube": adapter}, NOW + timedelta(minutes=31))
    assert conn.execute("SELECT status FROM publications WHERE clip_id = 1").fetchone()[0] == "published"


def test_gives_up_after_max_attempts(db):
    _, conn = db
    c = campaign(per_day=5)
    adapter = MockAdapter(fail=99)
    enqueue(conn, c, NOW, random.Random(3))
    conn.execute("DELETE FROM publications WHERE clip_id != 1")  # only clip 1 so the retry schedule is deterministic
    conn.commit()
    run_due(conn, c, {"youtube": adapter}, NOW)
    run_due(conn, c, {"youtube": adapter}, NOW + timedelta(minutes=31))
    run_due(conn, c, {"youtube": adapter}, NOW + timedelta(minutes=92))
    row = conn.execute("SELECT status, attempts, last_error FROM publications WHERE clip_id = 1").fetchone()
    assert row[0] == "failed" and row[1] == 3 and row[2] == "mock failure"


def test_unimplemented_platform_is_skipped(db):
    _, conn = db
    c = campaign(platform="instagram")
    enqueue(conn, c, NOW, random.Random(3))
    msgs = run_due(conn, c, {"youtube": MockAdapter()}, NOW)
    assert msgs == ["instagram/main: адаптер платформы не реализован, пропуск"]
    assert {s for _, s in statuses(conn)} == {"queued"}
