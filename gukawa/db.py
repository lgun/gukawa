from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3
from pathlib import Path
import time

ACTIVE = ("downloading", "extracting", "translating")
FIELDS = {"title", "title_ko", "caption", "episode_number", "published_at", "status", "error", "duration", "attempts", "available_at", "input_language"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS episodes (
                    id TEXT PRIMARY KEY, source_url TEXT NOT NULL, title TEXT NOT NULL,
                    title_ko TEXT, caption TEXT NOT NULL DEFAULT '', episode_number INTEGER, published_at TEXT,
                    status TEXT NOT NULL DEFAULT 'queued', error TEXT,
                    duration REAL, attempts INTEGER NOT NULL DEFAULT 0,
                    available_at REAL NOT NULL DEFAULT 0, input_language TEXT NOT NULL DEFAULT 'en',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS episode_status ON episodes(status, available_at);
                CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def enqueue(self, episode_id: str, source_url: str, title: str, **values) -> bool:
        if not values.keys() <= FIELDS:
            raise ValueError("Invalid episode field")
        columns = ["id", "source_url", "title", "created_at", "updated_at", *values]
        arguments = [episode_id, source_url, title, utc_now(), utc_now(), *values.values()]
        with self.connect() as db:
            result = db.execute(
                f"INSERT OR IGNORE INTO episodes ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                arguments,
            )
        return bool(result.rowcount)

    def update(self, episode_id: str, **values):
        if not values:
            return
        if not values.keys() <= FIELDS:
            raise ValueError("Invalid episode field")
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.connect() as db:
            db.execute(f"UPDATE episodes SET {assignments}, updated_at=? WHERE id=?", (*values.values(), utc_now(), episode_id))

    def get(self, episode_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM episodes WHERE id=?", (episode_id,)).fetchone()
            return dict(row) if row else None

    def watchable(self):
        """Episodes with Korean subtitles, in episode order, for the viewer and its prev/next navigation."""
        with self.connect() as db:
            rows = db.execute("SELECT id, episode_number, title, title_ko, status FROM episodes "
                              "WHERE status IN ('review','ready') AND episode_number IS NOT NULL "
                              "ORDER BY episode_number, id").fetchall()
            return [dict(row) for row in rows]

    def by_number(self, number: int):
        with self.connect() as db:
            row = db.execute("SELECT * FROM episodes WHERE episode_number=? AND status IN ('review','ready') "
                             "ORDER BY updated_at DESC LIMIT 1", (number,)).fetchone()
            return dict(row) if row else None

    def list(self, *, public=True, search="", limit=100, offset=0):
        where = ["status='ready'"] if public else ["1=1"]
        args: list = []
        if search:
            where.append("(COALESCE(title_ko,'') LIKE ? OR title LIKE ? OR CAST(episode_number AS TEXT)=?)")
            args += [f"%{search}%", f"%{search}%", search]
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM episodes WHERE {' AND '.join(where)} ORDER BY COALESCE(published_at, created_at) DESC, id DESC LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def count_ready(self):
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM episodes WHERE status='ready'").fetchone()[0]

    def counts(self):
        with self.connect() as db:
            return {row["status"]: row["count"] for row in db.execute("SELECT status, COUNT(*) AS count FROM episodes GROUP BY status")}

    def known_ids(self):
        with self.connect() as db:
            return {row[0] for row in db.execute("SELECT id FROM episodes")}

    def all_stored(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM episodes WHERE status IN ('source_ready','review','ready')")]

    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM episodes WHERE status='queued' AND available_at<=? ORDER BY created_at LIMIT 1", (time.time(),)).fetchone()
            if not row:
                return None
            db.execute("UPDATE episodes SET status='downloading', attempts=attempts+1, error=NULL, updated_at=? WHERE id=?", (utc_now(), row["id"]))
            result = dict(row)
            result["attempts"] += 1
            return result

    def recover(self):
        # Called only while holding the single-worker filesystem lock.
        with self.connect() as db:
            db.execute("UPDATE episodes SET status='queued', updated_at=? WHERE status IN ('downloading','extracting','translating')", (utc_now(),))

    def transition(self, episode_id: str, before: tuple[str, ...], after: str, *, reset=False):
        with self.connect() as db:
            extra = ", attempts=0, available_at=0, error=NULL" if reset else ""
            placeholders = ",".join("?" for _ in before)
            result = db.execute(f"UPDATE episodes SET status=?, updated_at=?{extra} WHERE id=? AND status IN ({placeholders})", (after, utc_now(), episode_id, *before))
            return result.rowcount > 0

    def set_state(self, key: str, value: str):
        with self.connect() as db:
            db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_state(self, key: str, default=""):
        with self.connect() as db:
            row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
            return row[0] if row else default

    def consume_sync_request(self) -> str:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT value FROM state WHERE key='sync_requested'").fetchone()
            db.execute("DELETE FROM state WHERE key='sync_requested'")
            return row[0] if row else ""
