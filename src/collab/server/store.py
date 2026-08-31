"""Durable session state.

One append-only ``events`` table is the backbone: ``seq`` is the primary key,
it is handed out on append, and it doubles as the SSE ``id:``.  Resume after a
disconnect, ``/history`` backfill, and surviving a hub restart all fall out of
that single design.

**Identity is an id, never a display name.**  Names are what people see and
they change; routing a message or a permission check on one breaks the instant
someone renames themselves.  Every participant gets a stable ``p_...`` id, and
``participant_names`` remembers every name they have ever held, so a reference
someone still holds to an old name resolves to the right person.

Everything here is synchronous sqlite3 called through ``asyncio.to_thread`` by
the callers, so we take no async-driver dependency.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..protocol import Envelope

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    room         TEXT,
    sender       TEXT NOT NULL,
    recipient    TEXT,
    sender_id    TEXT,
    recipient_id TEXT,
    ts           TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_room ON events(room, seq);

CREATE TABLE IF NOT EXISTS participants (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL UNIQUE,
    is_host    INTEGER NOT NULL DEFAULT 0,
    joined_at  REAL NOT NULL,
    last_seen  REAL NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0,
    meta       TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS participant_names (
    name           TEXT PRIMARY KEY,
    participant_id TEXT NOT NULL,
    claimed_at     REAL NOT NULL
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
    sender_id    TEXT,
    recipient_id TEXT,
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
    owner_id   TEXT,
    room       TEXT,
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    detail     TEXT NOT NULL DEFAULT ''
);
"""


def _ensure_columns(db: sqlite3.Connection, table: str,
                    columns: dict[str, str]) -> None:
    """Add any missing columns to a table that already exists.

    ``CREATE TABLE IF NOT EXISTS`` cannot widen a table, so a database written
    by an older version keeps its old columns forever.  Re-running this is a
    no-op, which is why there is no schema version to track.
    """
    # Identifiers cannot be parameterised in SQLite, and every name here is a
    # literal from this module — never user input.
    have = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in have:
            try:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            except sqlite3.OperationalError as exc:
                # Another process widened the same file between our PRAGMA
                # read and this write.  Its column is the one we wanted.
                if "duplicate column" not in str(exc).lower():
                    raise


def _migrate(db: sqlite3.Connection) -> None:
    """Bring an existing database up to the schema this version expects."""
    _ensure_columns(db, "events", {"sender_id": "TEXT", "recipient_id": "TEXT"})
    # No back-fill: a row written before these columns cannot prove who its two
    # ends were, so it stays NULL and the route fails it closed.
    _ensure_columns(db, "files", {"sender_id": "TEXT", "recipient_id": "TEXT"})
    _ensure_columns(db, "tasks", {"owner_id": "TEXT"})
    # Tasks *are* back-filled, unlike files: a task board is durable, and a
    # claimed task with no owner id would be actionable by anyone — wider than
    # the hole this closes.  The WHERE is what makes re-running a no-op; the
    # correlated sub-query leaves owner_id NULL where the name does not resolve.
    db.execute(
        "UPDATE tasks SET owner_id = ("
        "  SELECT participant_id FROM participant_names"
        "  WHERE participant_names.name = tasks.owner)"
        " WHERE owner_id IS NULL AND owner IS NOT NULL"
    )
    # That UPDATE is the only DML here, and sqlite3 opens an implicit
    # transaction for it.  Close it, or the caller's `PRAGMA journal_mode=WAL`
    # fails with "cannot change into wal mode from within a transaction".
    db.commit()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_participant_id() -> str:
    return "p_" + uuid.uuid4().hex[:12]


@dataclass
class Participant:
    id: str
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
            _migrate(self._db)
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
                "INSERT INTO events (kind, room, sender, recipient, sender_id,"
                " recipient_id, ts, payload) VALUES (?,?,?,?,?,?,?,?)",
                (env.kind, env.room, env.sender, env.to,
                 env.sender_id, env.to_id, env.ts, ""),
            )
            env.seq = int(cur.lastrowid)
            self._db.execute(
                "UPDATE events SET payload=? WHERE seq=?",
                (json.dumps(env.to_dict()), env.seq),
            )
            self._db.commit()
        return env

    def since(self, seq: int, *, viewer: str | None = None, limit: int = 500) -> list[Envelope]:
        """Events after ``seq`` that ``viewer`` (a participant id) may see."""
        with self._lock:
            rows = self._db.execute(
                "SELECT payload, recipient_id, sender_id FROM events WHERE seq > ?"
                " ORDER BY seq LIMIT ?",
                (seq, limit),
            ).fetchall()
        out = []
        for r in rows:
            if not _visible_to(r["recipient_id"], r["sender_id"], viewer):
                continue
            out.append(Envelope.from_dict(json.loads(r["payload"])))
        return out

    def history(self, *, room: str | None = None, viewer: str | None = None,
                limit: int = 50) -> list[Envelope]:
        sql = "SELECT payload, recipient_id, sender_id FROM events"
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
            if not _visible_to(r["recipient_id"], r["sender_id"], viewer):
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
                        meta: dict[str, Any] | None = None) -> Participant:
        """Insert a participant, suffixing the name if it is already taken."""
        now = time.time()
        pid = new_participant_id()
        with self._lock:
            final = name
            n = 2
            # Callers reject a name a live participant holds, so this loop only
            # guards the table's UNIQUE constraint. A name left behind by
            # someone who renamed away is free to claim.
            while self._db.execute(
                "SELECT 1 FROM participants WHERE name=? AND revoked=0", (final,)
            ).fetchone():
                final = f"{name}-{n}"
                n += 1
            self._db.execute(
                "INSERT INTO participants (id, name, token_hash, is_host, joined_at,"
                " last_seen, meta) VALUES (?,?,?,?,?,?,?)",
                (pid, final, token_hash(token), int(is_host), now, now,
                 json.dumps(meta or {})),
            )
            self._db.execute(
                "INSERT OR REPLACE INTO participant_names (name, participant_id,"
                " claimed_at) VALUES (?,?,?)",
                (final, pid, now),
            )
            self._db.commit()
        return Participant(id=pid, name=final, is_host=is_host, joined_at=now,
                           last_seen=now, revoked=False, meta=dict(meta or {}))

    def name_taken(self, name: str, *, except_id: str = "") -> bool:
        """Is this name currently held by somebody still in the session?

        Only *current* names count: a name freed by a rename, or belonging to
        someone who was removed, is available again.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT id FROM participants WHERE name=? AND revoked=0", (name,)
            ).fetchone()
        return bool(row) and row["id"] != except_id

    def resolve_name(self, name: str) -> str | None:
        """Find a participant id from a name, current or historical.

        Whoever holds the name *now* wins: if somebody renamed away and a new
        arrival took the name, that name means the new arrival. Only when no
        one currently holds it does it fall back to the last person who did,
        which is what keeps a stale reference from before a rename working.
        """
        if not name:
            return None
        with self._lock:
            current = self._db.execute(
                "SELECT id FROM participants WHERE name=? AND revoked=0", (name,)
            ).fetchone()
            if current:
                return str(current["id"])
            historical = self._db.execute(
                "SELECT participant_id FROM participant_names WHERE name=?", (name,)
            ).fetchone()
        return str(historical["participant_id"]) if historical else None

    def participant_by_id(self, participant_id: str) -> Participant | None:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM participants WHERE id=?", (participant_id,)
            ).fetchone()
        return _to_participant(row) if row else None

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

    def touch(self, participant_id: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE participants SET last_seen=? WHERE id=?",
                (time.time(), participant_id),
            )
            self._db.commit()

    def update_meta(self, participant_id: str, meta: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE participants SET meta=? WHERE id=?",
                (json.dumps(meta), participant_id),
            )
            self._db.commit()

    def revoke(self, participant_id: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE participants SET revoked=1 WHERE id=? AND is_host=0",
                (participant_id,),
            )
            self._db.commit()
        return cur.rowcount > 0

    def rename(self, participant_id: str, new: str) -> str:
        """Change the display name. The id — and so all routing — is untouched."""
        now = time.time()
        with self._lock:
            final, n = new, 2
            while self._db.execute(
                "SELECT 1 FROM participants WHERE name=? AND id<>? AND revoked=0",
                (final, participant_id),
            ).fetchone():
                final = f"{new}-{n}"
                n += 1
            self._db.execute(
                "UPDATE participants SET name=? WHERE id=?", (final, participant_id)
            )
            # Keep the old name pointing here so references to it still resolve.
            self._db.execute(
                "INSERT OR REPLACE INTO participant_names (name, participant_id, claimed_at)"
                " VALUES (?,?,?)",
                (final, participant_id, now),
            )
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

    def clear_invites(self) -> int:
        """Retire every invite issued so far.

        Used when a session is resumed: the conversation carries over, the way
        in does not. An old link should not still open the door.
        """
        with self._lock:
            cur = self._db.execute("DELETE FROM invites")
            self._db.commit()
        return cur.rowcount

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
                    owner_id: str | None, room: str | None, created_by: str,
                    detail: str = "") -> dict[str, Any]:
        now = time.time()
        with self._lock:
            existing = self._db.execute(
                "SELECT * FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if existing is None:
                self._db.execute(
                    "INSERT INTO tasks (id,title,state,owner,owner_id,room,created_by,"
                    "created_at,updated_at,detail)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (task_id, title, state, owner, owner_id, room, created_by,
                     now, now, detail),
                )
            else:
                self._db.execute(
                    "UPDATE tasks SET title=?, state=?, owner=?, owner_id=?, updated_at=?,"
                    " detail=? WHERE id=?",
                    (title or existing["title"], state, owner, owner_id, now,
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
                 recipient: str | None, room: str | None,
                 sender_id: str, recipient_id: str | None) -> dict[str, Any]:
        # The ids are required, not defaulted: they are what authorization reads,
        # and a default is how the next caller silently re-opens the hole.
        with self._lock:
            self._db.execute(
                "INSERT INTO files"
                " (id,name,size,sha256,sender,recipient,sender_id,recipient_id,room,created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (file_id, name, size, sha256, sender, recipient,
                 sender_id, recipient_id, room, time.time()),
            )
            self._db.commit()
            row = self._db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return dict(row)

    def get_file(self, file_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return dict(row) if row else None

    def files(self, *, include_gone: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM files"
        if not include_gone:
            sql += " WHERE state='available'"
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._db.execute(sql).fetchall()
        return [dict(r) for r in rows]

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


def _visible_to(recipient_id: str | None, sender_id: str | None,
                viewer_id: str | None) -> bool:
    """DMs are visible only to their two ends; everything else is room-wide.

    Compared by id, so a rename on either end changes nothing.
    """
    if not recipient_id:
        return True
    if viewer_id is None:
        return True
    return viewer_id in (recipient_id, sender_id)


def _to_participant(row: sqlite3.Row) -> Participant:
    return Participant(
        id=row["id"],
        name=row["name"],
        is_host=bool(row["is_host"]),
        joined_at=row["joined_at"],
        last_seen=row["last_seen"],
        revoked=bool(row["revoked"]),
        meta=json.loads(row["meta"] or "{}"),
    )
