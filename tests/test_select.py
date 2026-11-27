import json
import sqlite3

import numpy as np
import pytest

from clipper.core.config import Campaign, Settings
from clipper.core.db import connect
from clipper.select import select as sel
from clipper.select.prefilter import prefilter

VALID = [
    {"start": 10.0, "end": 45.0, "hook_start": 10.0, "hook_end": 13.0, "hook_text": "why does this matter",
     "score": 80, "hook_type": "conflict", "reason": "strong conflict", "title_overlay": "Stop waiting",
     "description": "d", "hashtags": ["#clip"], "mentions": []},
    {"start": 50.0, "end": 85.0, "hook_start": 50.0, "hook_end": 53.0, "hook_text": "x",
     "score": 50, "hook_type": "conflict", "reason": "weak", "title_overlay": "t", "description": "d",
     "hashtags": [], "mentions": []},
]


def campaign(limit=5):
    return Campaign(id="demo", name="Demo", mode="clip_source", sources=["x.mp4"],
                    limits={"max_claude_requests_per_source": limit})


def fake_transcript(duration=120.0):
    words = [{"start": i * 0.5, "end": i * 0.5 + 0.4, "word": "word"} for i in range(int(duration * 2))]
    words[2]["word"] = "why?"  # t=1.0 s, inside the first 3 s of the window starting at 0
    words[3]["word"] = "90%"
    return {"duration_s": duration, "segments": [{"words": words}]}


def test_prefilter_skips_windows_without_hook_in_first_3s():
    words = fake_transcript()["segments"][0]["words"]
    out = prefilter(words, np.zeros(120 * 16000, dtype=np.float32), 16000, 120.0, top_n=40)
    assert out, "должны остаться окна с вопросом/цифрой в первые 3 с"
    assert all(w.start == 0.0 for w in out)  # only the window starting at 0 has a strong word in its first 3 s


def test_prefilter_respects_top_n_and_no_overlap():
    words = fake_transcript()["segments"][0]["words"]
    out = prefilter(words, np.zeros(120 * 16000, dtype=np.float32), 16000, 120.0, top_n=3)
    assert len(out) <= 3
    for i, a in enumerate(out):
        for b in out[i + 1:]:
            inter = min(a.end, b.end) - max(a.start, b.start)
            assert inter <= 0.5 * min(a.end - a.start, b.end - b.start) + 1e-9


def test_prefilter_empty_for_short_video():
    assert prefilter([], np.zeros(10, dtype=np.float32), 16000, 10.0, top_n=5) == []


def test_check_clips_flags_bad_hook_and_length():
    bad = [dict(VALID[0], hook_end=20.0)]  # hook 10 s long
    clips, errors = sel.check_clips(json.dumps(bad), campaign())
    assert clips == [] and any("тизер" in e for e in errors)
    short = [dict(VALID[0], end=20.0, hook_end=13.0)]  # 10 s clip
    _, errors = sel.check_clips(json.dumps(short), campaign())
    assert any("длина клипа" in e for e in errors)


def test_retry_then_success_and_cache(monkeypatch, tmp_path):
    from clipper.select.prefilter import Window

    batch = [Window(0.0, 45.0, "text", 1.0)]
    answers = iter(["oops", json.dumps(VALID)])
    monkeypatch.setattr(sel, "ask_claude", lambda p: next(answers))
    monkeypatch.setattr(sel, "CACHE_DIR", tmp_path)
    conn = connect(tmp_path / "c.db")
    calls = [0]
    clips = sel.select_batch(campaign(), batch, calls, conn, 100)
    assert len(clips) == 2 and calls[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM claude_calls").fetchone()[0] == 2

    monkeypatch.setattr(sel, "ask_claude", lambda p: pytest.fail("кэш должен отдать результат без вызова Claude"))
    assert sel.select_batch(campaign(), batch, calls, conn, 100) == clips


def test_request_limit_logged_not_raised(monkeypatch, tmp_path, caplog):
    from clipper.select.prefilter import Window

    monkeypatch.setattr(sel, "ask_claude", lambda p: "garbage")
    monkeypatch.setattr(sel, "CACHE_DIR", tmp_path)
    conn = connect(tmp_path / "c.db")
    calls = [0]
    clips = sel.select_batch(campaign(limit=1), [Window(0.0, 45.0, "t", 1.0)], calls, conn, 100)
    assert clips == [] and calls[0] == 1
    assert "лимит" in caplog.text


def test_daily_claude_limit_stops_requests(monkeypatch, tmp_path, caplog):
    from clipper.select.prefilter import Window

    monkeypatch.setattr(sel, "ask_claude", lambda p: pytest.fail("суточный лимит должен остановить вызов"))
    monkeypatch.setattr(sel, "CACHE_DIR", tmp_path)
    conn = connect(tmp_path / "c.db")
    conn.execute("INSERT INTO claude_calls DEFAULT VALUES")
    conn.commit()
    assert sel.select_batch(campaign(), [Window(0.0, 45.0, "t", 1.0)], [0], conn, 1) == []
    assert "суточный лимит" in caplog.text


def test_select_source_end_to_end(monkeypatch, tmp_path):
    monkeypatch.setattr(sel, "CACHE_DIR", tmp_path / "sel")
    monkeypatch.setattr(sel, "transcribe", lambda path, model: fake_transcript())
    monkeypatch.setattr(sel, "decode_audio", lambda path, sampling_rate: np.zeros(120 * 16000, dtype=np.float32))
    monkeypatch.setattr(sel, "ask_claude", lambda p: json.dumps(VALID))
    conn = connect(tmp_path / "clipper.db")
    c = campaign()

    res = sel.select_source(c, "abc", tmp_path / "v.mp4", Settings(), conn)
    assert res.reason is None and res.inserted == 1  # score 50 is below min_score 70
    row = conn.execute("SELECT score, hook_type, status, hashtags FROM clips").fetchone()
    assert row == (80, "conflict", "selected", '["#clip"]')

    again = sel.select_source(c, "abc", tmp_path / "v.mp4", Settings(), conn)
    assert again.inserted == 0
    assert conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0] == 1


def test_select_source_reports_reason_when_no_speech(monkeypatch, tmp_path):
    monkeypatch.setattr(sel, "transcribe", lambda path, model: {"duration_s": 60.0, "segments": [{"words": []}]})
    conn = connect(tmp_path / "clipper.db")
    res = sel.select_source(campaign(), "abc", tmp_path / "v.mp4", Settings(), conn)
    assert res.inserted == 0 and res.reason == "в транскрипте нет речи"
