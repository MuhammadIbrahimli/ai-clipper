import subprocess

import pytest

from clipper.core.config import Campaign
from clipper.render.gate import check_clip

DESC = "A strong hook. #clip @brand"


def campaign(**kw):
    base = dict(id="demo", name="Demo", mode="clip_source", sources=["x.mp4"], clip={"min_s": 1, "max_s": 10},
                required_hashtags=["#clip"], required_mentions=["@brand"])
    return Campaign(**(base | kw))


def ff(args, out):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, str(out)], check=True)
    return out


@pytest.fixture
def good(tmp_path, test_video):
    # testsrc video with a sine track: no black frames, has audio, 2 s long
    return test_video


def test_good_clip_passes(good):
    assert check_clip(campaign(), good, 0.0, 3.0, True, DESC) == []


def test_clip_without_audio_is_rejected(tmp_path, good):
    silent = ff(["-i", str(good), "-an", "-c:v", "copy"], tmp_path / "silent.mp4")
    reasons = check_clip(campaign(), silent, 0.0, 3.0, True, DESC)
    assert "нет аудио" in reasons


def test_black_frames_are_rejected(tmp_path):
    black = ff(["-f", "lavfi", "-i", "color=c=black:s=320x240:d=2:r=30",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-shortest",
                "-c:v", "libx264", "-c:a", "aac"], tmp_path / "black.mp4")
    reasons = check_clip(campaign(), black, 0.0, 3.0, True, DESC)
    assert any(r.startswith("чёрный кадр") for r in reasons)


def test_missing_required_tag_is_rejected(good):
    reasons = check_clip(campaign(), good, 0.0, 3.0, True, "no tags here")
    assert "нет тега из ТЗ в описании: #clip" in reasons
    assert "нет упоминания из ТЗ в описании: @brand" in reasons


def test_bad_teaser_and_duration_are_rejected(good):
    reasons = check_clip(campaign(clip={"min_s": 30, "max_s": 55}), good, 0.0, 3.0, False, DESC)
    assert any(r.startswith("длительность") for r in reasons)
    assert "тизер отсутствует или не 2-4 с" in reasons


def test_unreadable_file_is_rejected_with_reason(tmp_path):
    reasons = check_clip(campaign(), tmp_path / "missing.mp4", 0.0, 3.0, True, DESC)
    assert len(reasons) == 1 and reasons[0].startswith("файл не читается")
