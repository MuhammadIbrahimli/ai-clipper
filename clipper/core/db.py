import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    campaign TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    path TEXT NOT NULL,
    duration_s REAL NOT NULL,
    fps REAL NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    has_audio INTEGER NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (campaign, sha256)
);
CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY,
    campaign TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    start REAL NOT NULL,
    end REAL NOT NULL,
    hook_start REAL NOT NULL,
    hook_end REAL NOT NULL,
    hook_text TEXT NOT NULL,
    hook_type TEXT NOT NULL,
    score INTEGER NOT NULL,
    reason TEXT NOT NULL,
    title_overlay TEXT NOT NULL,
    description TEXT NOT NULL,
    hashtags TEXT NOT NULL,
    mentions TEXT NOT NULL,
    status TEXT NOT NULL,
    render_path TEXT,
    subtitle_preset TEXT,
    speed_factor REAL,
    has_teaser INTEGER,
    has_callouts INTEGER,
    zoom_enabled INTEGER,
    duration_s REAL,
    reject_reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (campaign, source_sha256, start, end)
);
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY,
    campaign TEXT NOT NULL,
    clip_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    account_label TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    scheduled_at TEXT NOT NULL,
    published_at TEXT,
    external_id TEXT,
    last_error TEXT,
    UNIQUE (clip_id, platform, account_label)
);
CREATE TABLE IF NOT EXISTS metrics (
    publication_id INTEGER PRIMARY KEY,
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS claude_calls (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY,
    campaign TEXT NOT NULL,
    kind TEXT NOT NULL,
    args TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL,
    log TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect(path: Path, check_same_thread: bool = True) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.executescript(SCHEMA)
    return conn
