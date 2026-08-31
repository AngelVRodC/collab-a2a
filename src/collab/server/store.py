"""Durable session state.

One append-only ``events`` table is the backbone: ``seq`` is the primary key,
it is handed out on append, and it doubles as the SSE ``id:``.  Resume after a
disconnect, ``/history`` backfill, and surviving a hub restart all fall out of
that single design.

Everything here is synchronous sqlite3 called through ``asyncio.to_thread`` by
the callers, so we take no async-driver dependency.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..protocol import Envelope

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind      TEXT NOT NULL,
    room      TEXT,
    sender    TEXT NOT NULL,
    recipient TEXT,
    ts        TEXT NOT NULL,
    payload   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_room ON events(room, seq);

CREATE TABLE IF NOT EXISTS participants (
    name       TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    is_host    INTEGER NOT NULL DEFAULT 0,
    joined_at  REAL NOT NULL,
    last_seen  REAL NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0,
    meta       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS invites (
    code_hash  TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL,
    max_uses   INTEGER NOT NULL DEFAULT 0,
    uses       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rooms (
    name       TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    created_by TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS files (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    size       INTEGER NOT NULL,
    sha256     TEXT NOT NULL,
    sender     TEXT NOT NULL,
    recipient  TEXT,
    room       TEXT,
    created_at REAL NOT NULL,
    acked_at   REAL,
    acked_by   TEXT,
    state      TEXT NOT NULL DEFAULT 'available'
);

CREATE TABLE IF NOT EXISTS tasks (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    state      TEXT NOT NULL,
    owner      TEXT,
    room       TEXT,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);
"""


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Participant:
    name: str
    is_host: bool
    joined_at: float
    last_seen: float
    revoked: bool
    meta: dict[str, Any]


class Store:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- events --------------------------------------------------------------

    def append(self, env: Envelope) -> Envelope:
        """Persist an event and stamp it with its ``seq``.

        Called before fan-out, so a message can never be delivered with a seq
        that isn't already durable.
        """
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events (kind, room, sender, recipient, ts, payload)"
                " VALUES (?,?,?,?,?,?)",
                (env.kind, env.room, env.sender, env.to, env.ts, ""),
            )
            env.seq = int(cur.lastrowid)
            self._db.execute(
                "UPDATE events SET payload=? WHERE seq=?",
                (json.dumps(env.to_dict()), env.seq),
            )
            self._db.commit()
        return env

    def since(self, seq: int, *, viewer: str | None = None, limit: int = 500) -> list[Envelope]:
        """Events after ``seq`` that ``viewer`` is allowed to see."""
        with self._lock:
            rows = self._db.execute(
                "SELECT payload, recipient, sender FROM events WHERE seq > ?"
                " ORDER BY seq LIMIT ?",
                (seq, limit),
            ).fetchall()
        out = []
        for r in rows:
            if not _visible_to(r["recipient"], r["sender"], viewer):
                continue
            out.append(Envelope.from_dict(json.loads(r["payload"])))
        return out

    def history(self, *, room: str | None = None, viewer: str | None = None,
                limit: int = 50) -> list[Envelope]:
        sql = "SELECT payload, recipient, sender FROM events"
        args: list[Any] = []
        if room:
            sql += " WHERE room = ?"
            args.append(room)
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(limit * 3)  # over-fetch, then filter for visibility
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        out = []
        for r in rows:
            if not _visible_to(r["recipient"], r["sender"], viewer):
                continue
            out.append(Envelope.from_dict(json.loads(r["payload"])))
            if len(out) >= limit:
                break
        return list(reversed(out))

    def max_seq(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COALESCE(MAX(seq), 0) AS m FROM events").fetchone()
        return int(row["m"])

    # --- participants --------------------------------------------------------

    def add_participant(self, name: str, token: str, *, is_host: bool = False,
                        meta: dict[str, Any] | None = None) -> str:
        """Insert a participant, suffixing the name if it is already taken."""
        now = time.time()
        with self._lock:
            final = name
            n = 2
            while self._db.execute(
                "SELECT 1 FROM participants WHERE name=?", (final,)
            ).fetchone():
                final = f"{name}-{n}"
                n += 1
            self._db.execute(
                "INSERT INTO participants (name, token_hash, is_host, joined_at, last_seen, meta)"
                " VALUES (?,?,?,?,?,?)",
                (final, token_hash(token), int(is_host), now, now, json.dumps(meta or {})),
            )
            self._db.commit()
        return final

    def participant_for_token(self, token: str) -> Participant | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM participants WHERE token_hash=?", (token_hash(token),)
            ).fetchone()
        if row is None or row["revoked"]:
            return None
        return _to_participant(row)

    def participants(self, *, include_revoked: bool = False) -> list[Participant]:
        sql = "SELECT * FROM participants"
        if not include_revoked:
            sql += " WHERE revoked=0"
        sql += " ORDER BY joined_at"
        with self._lock:
            rows = self._db.execute(sql).fetchall()
        return [_to_participant(r) for r in rows]

    def touch(self, name: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE participants SET last_seen=? WHERE name=?", (time.time(), name)
            )
            self._db.commit()

    def update_meta(self, name: str, meta: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE participants SET meta=? WHERE name=?", (json.dumps(meta), name)
            )
            self._db.commit()

    def revoke(self, name: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE participants SET revoked=1 WHERE name=? AND is_host=0", (name,)
            )
            self._db.commit()
        return cur.rowcount > 0

    def rename(self, old: str, new: str) -> str:
        with self._lock:
            final, n = new, 2
            while self._db.execute(
                "SELECT 1 FROM participants WHERE name=? AND name<>?", (final, old)
            ).fetchone():
                final = f"{new}-{n}"
                n += 1
            self._db.execute("UPDATE participants SET name=? WHERE name=?", (final, old))
            self._db.commit()
        return final

    # --- invites -------------------------------------------------------------

    def add_invite(self, code: str, *, ttl_seconds: float | None = None,
                   max_uses: int = 0) -> None:
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO invites (code_hash, created_at, expires_at, max_uses, uses)"
                " VALUES (?,?,?,?,0)",
                (token_hash(code), now, (now + ttl_seconds) if ttl_seconds else None, max_uses),
            )
            self._db.commit()

    def consume_invite(self, code: str) -> tuple[bool, str]:
        """Validate and spend one use.  Returns ``(ok, reason)``."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM invites WHERE code_hash=?", (token_hash(code),)
            ).fetchone()
            if row is None:
                return False, "unknown invite code"
            if row["expires_at"] is not None and time.time() > row["expires_at"]:
                return False, "invite expired"
            if row["max_uses"] and row["uses"] >= row["max_uses"]:
                return False, "invite already used the maximum number of times"
            self._db.execute(
                "UPDATE invites SET uses=uses+1 WHERE code_hash=?", (token_hash(code),)
            )
            self._db.commit()
        return True, ""

    # --- rooms ---------------------------------------------------------------

    def add_room(self, name: str, created_by: str = "") -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO rooms (name, created_at, created_by) VALUES (?,?,?)",
                (name, time.time(), created_by),
            )
            self._db.commit()

    def rooms(self) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT name FROM rooms ORDER BY created_at").fetchall()
        return [r["name"] for r in rows]

    # --- shared task board ----------------------------------------------------

    def upsert_task(self, task_id: str, *, title: str, state: str, owner: str | None,
                    room: str | None, created_by: str, detail: str = "") -> dict[str, Any]:
        now = time.time()
        with self._lock:
            existing = self._db.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if existing is None:
                self._db.execute(
                    "INSERT INTO tasks (id,title,state,owner,room,created_by,created_at,updated_at,detail)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (task_id, title, state, owner, room, created_by, now, now, detail),
                )
            else:
                self._db.execute(
                    "UPDATE tasks SET title=?, state=?, owner=?, updated_at=?, detail=? WHERE id=?",
                    (title or existing["title"], state, owner, now,
                     detail or existing["detail"], task_id),
                )
            self._db.commit()
            row = self._db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row)

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def tasks(self, *, open_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM tasks"
        if open_only:
            sql += (" WHERE state NOT IN ('TASK_STATE_COMPLETED','TASK_STATE_CANCELED',"
                    "'TASK_STATE_FAILED','TASK_STATE_REJECTED')")
        sql += " ORDER BY created_at"
        with self._lock:
            rows = self._db.execute(sql).fetchall()
        return [dict(r) for r in rows]

    # --- shared files -----------------------------------------------------------

    def add_file(self, file_id: str, *, name: str, size: int, sha256: str, sender: str,
                 recipient: str | None, room: str | None) -> dict[str, Any]:
        with self._lock:
            self._db.execute(
                "INSERT INTO files (id,name,size,sha256,sender,recipient,room,created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (file_id, name, size, sha256, sender, recipient, room, time.time()),
            )
            self._db.commit()
            row = self._db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return dict(row)

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return dict(row) if row else None

    def files(self, *, viewer: str | None = None, include_gone: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM files"
        if not include_gone:
            sql += " WHERE state='available'"
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._db.execute(sql).fetchall()
        out = [dict(r) for r in rows]
        if viewer is None:
            return out
        # A file addressed to someone is visible only to the two ends.
        return [f for f in out
                if not f["recipient"] or viewer in (f["recipient"], f["sender"])]

    def mark_file(self, file_id: str, state: str, *, acked_by: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE files SET state=?, acked_at=?, acked_by=? WHERE id=?",
                (state, time.time() if acked_by else None, acked_by, file_id),
            )
            self._db.commit()

    def expired_files(self, ttl_seconds: float) -> list[dict[str, Any]]:
        cutoff = time.time() - ttl_seconds
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM files WHERE state='available' AND created_at < ?", (cutoff,)
            ).fetchall()
        return [dict(r) for r in rows]


def _visible_to(recipient: str | None, sender: str, viewer: str | None) -> bool:
    """DMs are visible only to their two ends; everything else is room-wide."""
    if not recipient or viewer is None:
        return not recipient or viewer is None
    return viewer in (recipient, sender)


def _to_participant(row: sqlite3.Row) -> Participant:
    return Participant(
        name=row["name"],
        is_host=bool(row["is_host"]),
        joined_at=row["joined_at"],
        last_seen=row["last_seen"],
        revoked=bool(row["revoked"]),
        meta=json.loads(row["meta"] or "{}"),
    )
