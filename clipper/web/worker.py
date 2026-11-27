import json
import logging
import sqlite3
import threading
from pathlib import Path

from clipper import pipeline
from clipper.core.config import load_campaign, load_settings
from clipper.core.db import connect
from clipper.publish.base import Adapter

log = logging.getLogger(__name__)


class Worker:
    """Runs jobs from the SQLite `jobs` table one at a time on a background thread."""

    def __init__(self, base: Path, adapters: dict[str, Adapter] | None = None):
        self.base = base
        self.adapters = adapters
        self.db = base / "data" / "clipper.db"
        self.wake = threading.Event()

    def enqueue(self, campaign: str, kind: str, args: dict) -> int:
        conn = connect(self.db)
        try:
            with conn:
                job_id = conn.execute(
                    "INSERT INTO jobs (campaign, kind, args, status) VALUES (?, ?, ?, 'queued')",
                    (campaign, kind, json.dumps(args))).lastrowid
        finally:
            conn.close()
        self.wake.set()
        return job_id

    def start(self) -> None:
        conn = connect(self.db)
        with conn:  # a job still 'running' means the previous process died during it
            conn.execute("UPDATE jobs SET status = 'failed', log = log || 'прервано перезапуском\n'"
                         " WHERE status = 'running'")
        conn.close()
        threading.Thread(target=self._loop, daemon=True, name="clipper-worker").start()

    def _loop(self) -> None:
        while True:
            self.run_pending()
            self.wake.wait(timeout=5)
            self.wake.clear()

    def run_pending(self) -> None:
        while True:
            conn = connect(self.db)
            try:
                job = conn.execute(
                    "SELECT id, campaign, kind, args FROM jobs WHERE status = 'queued' ORDER BY id LIMIT 1").fetchone()
            finally:
                conn.close()
            if job is None:
                return
            self._run(*job)

    def _run(self, job_id: int, campaign_id: str, kind: str, args_json: str) -> None:
        conn = connect(self.db)

        def set_status(status: str) -> None:
            with conn:
                conn.execute("UPDATE jobs SET status = ?, updated_at = datetime('now') WHERE id = ?", (status, job_id))

        def say(msg: str) -> None:
            log.info("job %s: %s", job_id, msg)
            with conn:
                conn.execute("UPDATE jobs SET log = log || ?, updated_at = datetime('now') WHERE id = ?",
                             (msg + "\n", job_id))

        try:
            set_status("running")
            args = json.loads(args_json)
            campaign = load_campaign(self.base / "campaigns" / f"{campaign_id}.yaml")
            settings = load_settings(self.base / "config.yaml")
            pipeline.apply_tool_paths(settings)
            if kind == "process":
                pipeline.ingest_step(campaign, conn, say)
                pipeline.select_step(campaign, settings, conn, say)
                pipeline.render_step(campaign, settings, conn, say)
                pipeline.gate_step(campaign, conn, say)
            elif kind == "rerender":
                pipeline.render_step(campaign, settings, conn, say, [args["clip_id"]])
                pipeline.gate_step(campaign, conn, say)
            elif kind == "publish":
                pipeline.publish_step(campaign, settings, conn, say, args.get("now", False), self.adapters)
            else:
                raise ValueError(f"неизвестный тип задачи {kind}")
            set_status("done")
        except Exception as e:  # the worker must survive any failing job
            log.exception("job %s failed", job_id)
            say(f"ошибка: {e}")
            set_status("failed")
        finally:
            conn.close()
