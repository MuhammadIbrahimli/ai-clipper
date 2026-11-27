import csv
import io
import json
import re
import shutil
import sqlite3
import time
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from clipper.core.config import Campaign, ConfigError, Settings, load_campaign, load_settings, save_yaml, validation_lines
from clipper.core.db import connect
from clipper.publish.base import Adapter
from clipper.web.worker import Worker

ID_RE = re.compile(r"[a-z0-9_-]+")
SUBTITLE_PRESETS = ("classic", "bold", "karaoke")
ANALYTICS_KEYS = ("hook_type", "subtitle_preset", "speed_factor", "has_teaser", "has_callouts", "zoom_enabled")


def create_app(base: Path = Path("."), adapters: dict[str, Adapter] | None = None) -> FastAPI:
    """`base` is the project root; `adapters` overrides the real platform adapters (used in tests)."""
    db_path = base / "data" / "clipper.db"
    worker = Worker(base, adapters)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        worker.start()
        yield

    app = FastAPI(title="clipper", lifespan=lifespan)

    def conn_dep():
        # FastAPI opens and closes this generator on different threads; one request uses the connection sequentially
        conn = connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def campaign_path(cid: str) -> Path:
        if not ID_RE.fullmatch(cid):
            raise HTTPException(404, "кампания не найдена")
        return base / "campaigns" / f"{cid}.yaml"

    def load(cid: str) -> Campaign:
        path = campaign_path(cid)
        if not path.exists():
            raise HTTPException(404, "кампания не найдена")
        try:
            return load_campaign(path)
        except ConfigError as e:
            raise HTTPException(422, str(e)) from e

    def validate(model, data) -> object:
        try:
            return model.model_validate(data)
        except ValidationError as e:
            raise HTTPException(422, validation_lines(e)) from e

    def rows(conn, sql: str, *args) -> list[dict]:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]

    # --- campaigns -------------------------------------------------------
    @app.get("/api/campaigns")
    def list_campaigns():
        out = []
        for path in sorted((base / "campaigns").glob("*.yaml")):
            try:
                c = load_campaign(path)
                out.append({"id": c.id, "name": c.name, "mode": c.mode, "sources": len(c.sources)})
            except ConfigError as e:
                out.append({"id": path.stem, "error": str(e)})
        return out

    @app.get("/api/campaigns/{cid}")
    def get_campaign(cid: str):
        return load(cid).model_dump(mode="json", exclude_none=True)

    @app.put("/api/campaigns/{cid}")
    def put_campaign(cid: str, data: dict):
        if data.get("id") != cid:
            raise HTTPException(422, [f"поле 'id' должно совпадать с адресом кампании: '{cid}'"])
        campaign = validate(Campaign, data)
        save_yaml(campaign_path(cid), campaign.model_dump(mode="json", exclude_none=True))
        return {"ok": True}

    @app.post("/api/campaigns/{cid}/process")
    def process(cid: str):
        load(cid)
        return {"job": worker.enqueue(cid, "process", {})}

    @app.post("/api/campaigns/{cid}/publish-now")
    def publish_now(cid: str):
        load(cid)
        return {"job": worker.enqueue(cid, "publish", {"now": True})}

    # --- sources ---------------------------------------------------------
    @app.get("/api/campaigns/{cid}/sources")
    def sources(cid: str, conn=Depends(conn_dep)):
        settings = load_settings(base / "config.yaml")
        out = rows(conn, "SELECT id, sha256, path, duration_s, width, height, has_audio, status FROM sources"
                         " WHERE campaign = ? ORDER BY id", cid)
        for s in out:
            cache = base / "data" / "cache" / "transcripts" / f"{s['sha256']}-{settings.whisper_model}.json"
            s["transcribed"] = cache.exists()
        return out

    @app.post("/api/campaigns/{cid}/sources/upload")
    async def upload_sources(cid: str, files: list[UploadFile]):
        campaign = load(cid)
        folder = base / "data" / "sources" / cid
        folder.mkdir(parents=True, exist_ok=True)
        for f in files:
            name = Path(f.filename or "").name
            if not name:
                raise HTTPException(422, ["пустое имя файла"])
            dest = folder / name
            with dest.open("wb") as out:
                shutil.copyfileobj(f.file, out)
            if str(dest) not in map(str, campaign.sources):
                campaign.sources.append(dest)
        save_yaml(campaign_path(cid), campaign.model_dump(mode="json", exclude_none=True))
        return {"added": len(files)}

    @app.post("/api/campaigns/{cid}/sources/path")
    def add_source_path(cid: str, body: dict):
        campaign = load(cid)
        p = Path(str(body.get("path", "")))
        if not p.exists():
            raise HTTPException(422, [f"путь не найден: {p}"])
        if str(p) not in map(str, campaign.sources):
            campaign.sources.append(p)
        save_yaml(campaign_path(cid), campaign.model_dump(mode="json", exclude_none=True))
        return {"ok": True}

    # --- clips -----------------------------------------------------------
    @app.get("/api/campaigns/{cid}/clips")
    def clips(cid: str, conn=Depends(conn_dep)):
        out = rows(conn, "SELECT * FROM clips WHERE campaign = ? ORDER BY id", cid)
        for c in out:
            c["hashtags"] = json.loads(c["hashtags"])
            c["mentions"] = json.loads(c["mentions"])
        return out

    @app.patch("/api/clips/{clip_id}")
    def patch_clip(clip_id: int, body: dict, conn=Depends(conn_dep)):
        row = conn.execute("SELECT campaign, status FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "клип не найден")
        fields: dict = {}
        for key in ("title_overlay", "description"):
            if key in body:
                fields[key] = str(body[key])
        if "hashtags" in body:
            fields["hashtags"] = json.dumps(list(body["hashtags"]), ensure_ascii=False)
        if "subtitle_preset" in body:
            if body["subtitle_preset"] not in SUBTITLE_PRESETS:
                raise HTTPException(422, [f"subtitle_preset: одно из {', '.join(SUBTITLE_PRESETS)}"])
            fields["subtitle_preset"] = body["subtitle_preset"]
        if "status" in body:
            if body["status"] not in ("approved", "rejected"):
                raise HTTPException(422, ["status: approved или rejected"])
            if row["status"] not in ("ready", "approved", "rejected"):
                raise HTTPException(409, [f"клип в статусе '{row['status']}', решение пока нельзя принять"])
            fields["status"] = body["status"]
        rerender = bool(body.get("rerender"))
        if rerender:
            fields["status"] = "selected"  # render_step picks up 'selected' clips
        if fields:
            with conn:
                conn.execute(f"UPDATE clips SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                             (*fields.values(), clip_id))
        job = worker.enqueue(row["campaign"], "rerender", {"clip_id": clip_id}) if rerender else None
        return {"ok": True, "job": job}

    @app.get("/api/clips/{clip_id}/video")
    def clip_video(clip_id: int, conn=Depends(conn_dep)):
        row = conn.execute("SELECT render_path FROM clips WHERE id = ?", (clip_id,)).fetchone()
        if row is None or not row["render_path"] or not Path(row["render_path"]).exists():
            raise HTTPException(404, "видео не найдено")
        return FileResponse(row["render_path"], media_type="video/mp4")

    # --- publications, metrics, analytics ---------------------------------
    @app.get("/api/campaigns/{cid}/publications")
    def publications(cid: str, conn=Depends(conn_dep)):
        return rows(conn,
                    "SELECT p.id, p.clip_id, p.platform, p.account_label, p.status, p.attempts, p.scheduled_at,"
                    " p.published_at, p.external_id, p.last_error, c.title_overlay,"
                    " COALESCE(m.views, 0) AS views, COALESCE(m.likes, 0) AS likes, COALESCE(m.comments, 0) AS comments"
                    " FROM publications p JOIN clips c ON c.id = p.clip_id"
                    " LEFT JOIN metrics m ON m.publication_id = p.id"
                    " WHERE p.campaign = ? ORDER BY p.scheduled_at, p.id", cid)

    def upsert_metrics(conn, pid: int, views: int, likes: int, comments: int) -> None:
        with conn:
            conn.execute("INSERT INTO metrics (publication_id, views, likes, comments) VALUES (?, ?, ?, ?)"
                         " ON CONFLICT(publication_id) DO UPDATE SET views = excluded.views,"
                         " likes = excluded.likes, comments = excluded.comments", (pid, views, likes, comments))

    @app.put("/api/publications/{pid}/metrics")
    def put_metrics(pid: int, body: dict, conn=Depends(conn_dep)):
        if conn.execute("SELECT 1 FROM publications WHERE id = ?", (pid,)).fetchone() is None:
            raise HTTPException(404, "публикация не найдена")
        try:
            values = [int(body.get(k, 0)) for k in ("views", "likes", "comments")]
        except (TypeError, ValueError) as e:
            raise HTTPException(422, ["views, likes, comments: целые числа"]) from e
        if min(values) < 0:
            raise HTTPException(422, ["метрики не могут быть отрицательными"])
        upsert_metrics(conn, pid, *values)
        return {"ok": True}

    @app.post("/api/metrics/import")
    def import_metrics(file: UploadFile, conn=Depends(conn_dep)):
        # CSV with header: publication_id,views,likes,comments; sync so the SQLite connection stays on one thread
        reader = csv.DictReader(io.StringIO(file.file.read().decode("utf-8-sig")))
        count = 0
        for n, row in enumerate(reader, start=2):
            try:
                pid, views, likes, comments = (int(row.get(k) or 0) for k in ("publication_id", "views", "likes", "comments"))
            except ValueError as e:
                raise HTTPException(422, [f"строка {n}: publication_id, views, likes, comments должны быть целыми"]) from e
            if conn.execute("SELECT 1 FROM publications WHERE id = ?", (pid,)).fetchone() is None:
                raise HTTPException(422, [f"строка {n}: нет публикации с id {pid}"])
            upsert_metrics(conn, pid, views, likes, comments)
            count += 1
        return {"imported": count}

    @app.get("/api/campaigns/{cid}/analytics")
    def analytics(cid: str, by: str = "hook_type", conn=Depends(conn_dep)):
        if by not in ANALYTICS_KEYS:
            raise HTTPException(422, [f"by: одно из {', '.join(ANALYTICS_KEYS)}"])
        # `by` is checked against ANALYTICS_KEYS above, so interpolating the column name is safe
        sql = (f"SELECT c.{by} AS key, COUNT(p.id) AS publications, SUM(COALESCE(m.views, 0)) AS views,"
               " SUM(COALESCE(m.likes, 0)) AS likes, SUM(COALESCE(m.comments, 0)) AS comments"
               " FROM publications p JOIN clips c ON c.id = p.clip_id"
               " LEFT JOIN metrics m ON m.publication_id = p.id"
               f" WHERE p.campaign = ? AND p.status = 'published' GROUP BY c.{by} ORDER BY views DESC")
        return rows(conn, sql, cid)

    # --- settings and jobs -----------------------------------------------
    @app.get("/api/settings")
    def get_settings():
        return load_settings(base / "config.yaml").model_dump(mode="json", exclude_none=True)

    @app.put("/api/settings")
    def put_settings(data: dict):
        settings = validate(Settings, data)
        save_yaml(base / "config.yaml", settings.model_dump(mode="json", exclude_none=True))
        return {"ok": True}

    @app.get("/api/jobs")
    def jobs(conn=Depends(conn_dep)):
        return rows(conn, "SELECT id, campaign, kind, status, log, updated_at FROM jobs ORDER BY id DESC LIMIT 30")

    @app.get("/api/events")
    def events():
        # Server-sent events: pushes the job list whenever it changes, polled once a second.
        def stream():
            last = None
            while True:
                conn = connect(db_path)
                conn.row_factory = sqlite3.Row
                try:
                    payload = json.dumps(rows(conn, "SELECT id, campaign, kind, status, log, updated_at FROM jobs"
                                                    " ORDER BY id DESC LIMIT 30"), ensure_ascii=False)
                finally:
                    conn.close()
                if payload != last:
                    last = payload
                    yield f"data: {payload}\n\n"
                time.sleep(1)
        return StreamingResponse(stream(), media_type="text/event-stream")

    app.state.worker = worker
    dist = base / "web" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="ui")
    return app
