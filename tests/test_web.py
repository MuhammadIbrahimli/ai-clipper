import io
import json

import pytest
from fastapi.testclient import TestClient

from clipper.core.db import connect
from clipper.publish.base import PublishError
from clipper.web.app import create_app

CAMPAIGN = {
    "id": "demo", "name": "Demo", "mode": "clip_source", "sources": ["x.mp4"],
    "required_hashtags": ["#clip"],
    "target_accounts": [{"platform": "youtube", "account_label": "main"}],
    "limits": {"per_day": 5, "min_gap_minutes": 0},
}


class MockAdapter:
    platform = "youtube"

    def publish(self, clip, account):
        return f"yt-{clip.id}"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, adapters={"youtube": MockAdapter()})
    return TestClient(app)


def add_clip(base, status, description="Desc #clip"):
    conn = connect(base / "data" / "clipper.db")
    with conn:
        start = 100.0 * conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0]  # unique (start, end) per clip
        cur = conn.execute(
            "INSERT INTO clips (campaign, source_sha256, start, end, hook_start, hook_end, hook_text, hook_type,"
            " score, reason, title_overlay, description, hashtags, mentions, status, render_path)"
            " VALUES ('demo', 'abc', ?, ?, ?, ?, 'hook', 'conflict', 80, 'r', 'Title', ?, '[\"#clip\"]', '[]', ?, ?)",
            (start, start + 40, start, start + 3, description, status, str(base / "out.mp4")))
    conn.close()
    return cur.lastrowid


def test_campaign_form_writes_yaml_and_rejects_bad_fields(client, tmp_path):
    assert client.put("/api/campaigns/demo", json=CAMPAIGN).status_code == 200
    assert (tmp_path / "campaigns" / "demo.yaml").exists()
    assert client.get("/api/campaigns").json()[0]["id"] == "demo"

    bad = dict(CAMPAIGN, mode="nope")
    r = client.put("/api/campaigns/demo", json=bad)
    assert r.status_code == 422 and any("поле 'mode'" in line for line in r.json()["detail"])
    assert client.put("/api/campaigns/other", json=CAMPAIGN).status_code == 422
    assert client.get("/api/campaigns/..%2Fetc").status_code == 404


def test_approve_publish_and_status_visible(client, tmp_path):
    client.put("/api/campaigns/demo", json=CAMPAIGN)
    ready = add_clip(tmp_path, "ready")
    selected = add_clip(tmp_path, "selected")

    assert client.patch(f"/api/clips/{ready}", json={"status": "approved"}).status_code == 200
    assert client.patch(f"/api/clips/{selected}", json={"status": "approved"}).status_code == 409

    job = client.post("/api/campaigns/demo/publish-now").json()["job"]
    client.app.state.worker.run_pending()  # the real worker thread does this in the background
    jobs = {j["id"]: j for j in client.get("/api/jobs").json()}
    assert jobs[job]["status"] == "done" and "опубликовано, id yt-" in jobs[job]["log"]

    pubs = client.get("/api/campaigns/demo/publications").json()
    assert [(p["clip_id"], p["status"], p["platform"]) for p in pubs] == [(ready, "published", "youtube")]


def test_metrics_manual_import_and_analytics(client, tmp_path):
    client.put("/api/campaigns/demo", json=CAMPAIGN)
    clip = add_clip(tmp_path, "approved")
    client.post("/api/campaigns/demo/publish-now")
    client.app.state.worker.run_pending()
    pid = client.get("/api/campaigns/demo/publications").json()[0]["id"]

    assert client.put(f"/api/publications/{pid}/metrics", json={"views": 100, "likes": 5}).status_code == 200
    csv = f"publication_id,views,likes,comments\n{pid},250,9,1\n".encode()
    r = client.post("/api/metrics/import", files={"file": ("m.csv", io.BytesIO(csv), "text/csv")})
    assert r.json() == {"imported": 1}
    assert client.put(f"/api/publications/{pid}/metrics", json={"views": -1}).status_code == 422

    rows = client.get("/api/campaigns/demo/analytics", params={"by": "hook_type"}).json()
    assert rows == [{"key": "conflict", "publications": 1, "views": 250, "likes": 9, "comments": 1}]
    assert client.get("/api/campaigns/demo/analytics", params={"by": "description"}).status_code == 422
    assert clip > 0


def test_patch_rerender_queues_job_and_resets_status(client, tmp_path):
    client.put("/api/campaigns/demo", json=CAMPAIGN)
    clip = add_clip(tmp_path, "ready")
    r = client.patch(f"/api/clips/{clip}", json={"subtitle_preset": "bold", "rerender": True})
    assert r.status_code == 200 and r.json()["job"]
    row = client.get("/api/campaigns/demo/clips").json()[0]
    assert row["status"] == "selected" and row["subtitle_preset"] == "bold" and row["hashtags"] == ["#clip"]
    assert client.patch(f"/api/clips/{clip}", json={"subtitle_preset": "neon"}).status_code == 422


def test_sources_upload_and_folder(client, tmp_path):
    client.put("/api/campaigns/demo", json=CAMPAIGN)
    r = client.post("/api/campaigns/demo/sources/upload",
                    files=[("files", ("talk.mp4", io.BytesIO(b"data"), "video/mp4"))])
    assert r.json() == {"added": 1}
    assert (tmp_path / "data" / "sources" / "demo" / "talk.mp4").read_bytes() == b"data"
    assert client.post("/api/campaigns/demo/sources/path", json={"path": str(tmp_path)}).status_code == 200
    assert client.post("/api/campaigns/demo/sources/path", json={"path": str(tmp_path / "missing")}).status_code == 422
    sources = json.loads(json.dumps(client.get("/api/campaigns/demo").json()))["sources"]
    assert len(sources) == 3 and str(tmp_path) in sources[2]


def test_settings_roundtrip_and_validation(client, tmp_path):
    settings = client.get("/api/settings").json()
    assert settings["claude_daily_limit"] == 200
    settings["claude_daily_limit"] = 50
    assert client.put("/api/settings", json=settings).status_code == 200
    assert client.get("/api/settings").json()["claude_daily_limit"] == 50
    assert client.put("/api/settings", json={"whisper_model": "huge"}).status_code == 422
    assert (tmp_path / "config.yaml").exists()


def test_failed_publish_shows_error_in_publications(tmp_path):
    class Failing(MockAdapter):
        def publish(self, clip, account):
            raise PublishError("YouTube API: quotaExceeded")

    client = TestClient(create_app(tmp_path, adapters={"youtube": Failing()}))
    client.put("/api/campaigns/demo", json=CAMPAIGN)
    add_clip(tmp_path, "approved")
    client.post("/api/campaigns/demo/publish-now")
    client.app.state.worker.run_pending()
    pub = client.get("/api/campaigns/demo/publications").json()[0]
    assert pub["status"] == "queued" and "quotaExceeded" in pub["last_error"] and pub["attempts"] == 1
