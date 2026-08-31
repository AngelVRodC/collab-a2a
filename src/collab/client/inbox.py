"""The local inbox the daemon writes and the agent reads.

Two consumers are served from one write: a JSONL file that ``collab listen
--follow`` tails (what a Monitor watches), and a SQLite table that gives
``collab recv`` a durable cursor and remembers the last ``seq`` for resume.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

from ..protocol import Envelope

SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox (
    seq     INTEGER PRIMARY KEY,
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    sender  TEXT NOT NULL,
    payload TEXT NOT NULL,
    read    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Inbox:
    def __init__(self, directory: Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.jsonl = self.dir / "inbox.jsonl"
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.dir / "inbox.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def record(self, env: Envelope) -> bool:
        """Store one event.  Returns False if this seq was already stored.

        Replay after a reconnect can legitimately resend an event we already
        have; the primary key makes that a no-op rather than a duplicate
        notification.
        """
        if env.seq is None:
            return False
        with self._lock:
            existing = self._db.execute(
                "SELECT 1 FROM inbox WHERE seq=?", (env.seq,)
            ).fetchone()
            if existing:
                return False
            self._db.execute(
                "INSERT INTO inbox (seq, ts, kind, sender, payload) VALUES (?,?,?,?,?)",
                (env.seq, env.ts, env.kind, env.sender, json.dumps(env.to_dict())),
            )
            self._db.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_seq', ?)",
                (str(env.seq),),
            )
            self._db.commit()
        # The JSONL append is what a `collab listen --follow` tail sees.
        with self.jsonl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(env.to_dict(), ensure_ascii=False) + "\n")
            fh.flush()
        return True

    def last_seq(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key='last_seq'").fetchone()
        return int(row["value"]) if row else 0

    def unread_count(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) AS c FROM inbox WHERE read=0").fetchone()
        return int(row["c"])

    def take_unread(self, limit: int = 100, *, mark: bool = True) -> list[Envelope]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, payload FROM inbox WHERE read=0 ORDER BY seq LIMIT ?", (limit,)
            ).fetchall()
            if mark and rows:
                self._db.executemany(
                    "UPDATE inbox SET read=1 WHERE seq=?", [(r["seq"],) for r in rows]
                )
                self._db.commit()
        return [Envelope.from_dict(json.loads(r["payload"])) for r in rows]

    def all_events(self, limit: int = 100) -> list[Envelope]:
        with self._lock:
            rows = self._db.execute(
                "SELECT payload FROM inbox ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Envelope.from_dict(json.loads(r["payload"])) for r in reversed(rows)]

    def gaps(self) -> list[int]:
        """Missing seq values — used by the tests to prove nothing was dropped."""
        with self._lock:
            rows = self._db.execute("SELECT seq FROM inbox ORDER BY seq").fetchall()
        seqs = [r["seq"] for r in rows]
        if not seqs:
            return []
        return [n for n in range(seqs[0], seqs[-1] + 1) if n not in set(seqs)]
