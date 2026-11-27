import hashlib
import shutil

import pytest

from clipper.core.config import Campaign
from clipper.core.db import connect
from clipper.ingest.scan import IngestError, ingest_campaign, probe


def make_campaign(sources):
    return Campaign(id="demo", name="Demo", mode="clip_source", sources=sources)


def test_probe_reads_video(test_video):
    p = probe(test_video)
    assert (p.width, p.height) == (320, 240)
    assert p.fps == 30
    assert abs(p.duration_s - 2) < 0.2
    assert p.has_audio


def test_reregistration_creates_no_duplicates(tmp_path, test_video):
    src = tmp_path / "src"
    src.mkdir()
    shutil.copy(test_video, src / "a.mp4")
    shutil.copy(test_video, src / "copy_of_a.mp4")  # same bytes -> same sha256
    conn = connect(tmp_path / "clipper.db")

    first = ingest_campaign(make_campaign([src]), conn)
    second = ingest_campaign(make_campaign([src]), conn)

    assert sum(r.new for r in first) == 1
    assert sum(r.new for r in second) == 0
    rows = conn.execute("SELECT sha256, status FROM sources").fetchall()
    assert rows == [(hashlib.sha256(test_video.read_bytes()).hexdigest(), "registered")]


def test_missing_source_is_clear_error(tmp_path):
    conn = connect(tmp_path / "clipper.db")
    with pytest.raises(IngestError, match="источник не найден"):
        ingest_campaign(make_campaign([tmp_path / "nope"]), conn)
