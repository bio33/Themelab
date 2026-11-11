"""Local SQLite transactions; immutable pack files and separate user state."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

LOCK = threading.RLock()


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.assets = root / "assets"
        self.assets.mkdir(exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS packs (
                  id TEXT PRIMARY KEY, theme_id TEXT, version TEXT, digest TEXT,
                  manifest TEXT NOT NULL, issues TEXT NOT NULL, source TEXT NOT NULL,
                  created_at TEXT NOT NULL, UNIQUE(theme_id, version));
                CREATE TABLE IF NOT EXISTS state (
                  kind TEXT, id TEXT, data TEXT NOT NULL, PRIMARY KEY(kind,id));
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.root / "studio.sqlite3", timeout=20)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def find(self, theme_id, version):
        with self.connection() as db:
            row = db.execute(
                "SELECT id FROM packs WHERE theme_id=? AND version=?",
                (theme_id, version),
            ).fetchone()
            return self.pack(row["id"]) if row else None

    def pack(self, key):
        with self.connection() as db:
            row = db.execute("SELECT * FROM packs WHERE id=?", (key,)).fetchone()
        if not row:
            raise KeyError("Theme pack not found")
        data = dict(row)
        data["manifest"] = json.loads(data["manifest"])
        data["issues"] = json.loads(data["issues"])
        data["complete"] = not data["issues"]
        return data

    def packs(self):
        with self.connection() as db:
            ids = [
                r[0]
                for r in db.execute("SELECT id FROM packs ORDER BY created_at DESC")
            ]
        return [self.pack(key) for key in ids]

    def add(self, key, manifest, issues, source, digest):
        with self.connection() as db:
            db.execute(
                "INSERT INTO packs VALUES(?,?,?,?,?,?,?,?)",
                (
                    key,
                    manifest["theme"]["id"],
                    manifest["theme"]["version"],
                    digest,
                    json.dumps(manifest),
                    json.dumps(issues),
                    source,
                    now(),
                ),
            )

    def get(self, kind, key, default=None):
        with self.connection() as db:
            row = db.execute(
                "SELECT data FROM state WHERE kind=? AND id=?", (kind, key)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, kind, key, data):
        with self.connection() as db:
            db.execute(
                "INSERT INTO state VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET data=excluded.data",
                (kind, key, json.dumps(data)),
            )

    def all(self, kind):
        with self.connection() as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT data FROM state WHERE kind=?", (kind,))
            ]
