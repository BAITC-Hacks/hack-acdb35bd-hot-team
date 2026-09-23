import json
import threading

mutation_lock = threading.RLock()
import sqlite3
import uuid
from datetime import datetime, timezone
from .config import DATA

DB = DATA / "meetings.sqlite3"


def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init():
    with connect() as con:
        con.execute(
            "CREATE TABLE IF NOT EXISTS meetings (id TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )
        rows = con.execute("SELECT id, body FROM meetings").fetchall()
        for ident, body in rows:
            m = json.loads(body)
            changed = ensure_task_ids(m)
            if m.get("status") in ("queued", "processing"):
                m.update(
                    status="error",
                    error="Обработка прервана перезапуском. Запустите этап повторно.",
                )
                changed = True
            if changed:
                con.execute(
                    "UPDATE meetings SET body=? WHERE id=?",
                    (json.dumps(m, ensure_ascii=False), ident),
                )


def ensure_task_ids(m):
    changed = False
    for task in (m.get("protocol") or {}).get("tasks", []):
        if not task.get("id"):
            task["id"] = uuid.uuid4().hex
            changed = True
    return changed


def get(ident):
    with connect() as con:
        row = con.execute("SELECT body FROM meetings WHERE id=?", (ident,)).fetchone()
    return json.loads(row[0]) if row else None


def save(m):
    ensure_task_ids(m)
    m["updated_at"] = datetime.now(timezone.utc).isoformat()
    with connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO meetings VALUES (?,?)",
            (m["id"], json.dumps(m, ensure_ascii=False)),
        )
    return m


def all_meetings():
    with connect() as con:
        rows = con.execute("SELECT body FROM meetings").fetchall()
    return sorted(
        (json.loads(r[0]) for r in rows), key=lambda m: m["created_at"], reverse=True
    )
