"""Opening a database written by an older version must widen it.

``CREATE TABLE IF NOT EXISTS`` is a no-op against a table that already exists,
so commit ``3946c6b`` shipped ``events.sender_id``/``recipient_id`` that never
landed on any database created before it. ``Store.append`` names both columns
in its INSERT, so the first event a resumed session writes — the join
announcement itself — fails with ``table events has no column named
sender_id``. The database opens and lists cleanly; everything after that 500s.
"""

from __future__ import annotations

import json
import sqlite3
import threading

from collab.protocol import Envelope
from collab.server.store import SCHEMA, Store, _migrate

#: ``events`` exactly as it was before ``3946c6b`` — no id columns.
OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind      TEXT NOT NULL,
    room      TEXT,
    sender    TEXT NOT NULL,
    recipient TEXT,
    ts        TEXT NOT NULL,
    payload   TEXT NOT NULL
);
"""


def _old_db(path):
    """A database shaped the way the previous version left it."""
    db = sqlite3.connect(path)
    db.executescript(OLD_SCHEMA)
    env = Envelope(kind="message", sender="alice", room="general",
                   text="from before the migration", ts="2026-08-30T00:00:00Z")
    db.execute(
        "INSERT INTO events (kind, room, sender, recipient, ts, payload)"
        " VALUES (?, ?, ?, NULL, ?, ?)",
        (env.kind, env.room, env.sender, env.ts, json.dumps(env.to_dict())),
    )
    db.commit()
    db.close()
    return path


def _columns(path, table="events"):
    db = sqlite3.connect(path)
    try:
        return [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
    finally:
        db.close()


def test_an_old_database_is_widened_on_open(tmp_path):
    """The break shipped in 3946c6b: the reader named columns nobody had."""
    path = _old_db(tmp_path / "old.db")
    assert "sender_id" not in _columns(path)

    store = Store(path)
    try:
        assert "sender_id" in _columns(path)
        assert "recipient_id" in _columns(path)

        # Both readers name those columns explicitly, so both used to 500.
        assert [e.text for e in store.since(0)] == ["from before the migration"]
        assert [e.text for e in store.history()] == ["from before the migration"]
    finally:
        store.close()


def test_opening_an_already_migrated_database_is_a_no_op(tmp_path):
    """Re-running the migration is what lets it stay version-free."""
    path = _old_db(tmp_path / "old.db")
    Store(path).close()
    before = _columns(path)

    store = Store(path)
    try:
        assert _columns(path) == before
        assert [e.text for e in store.history()] == ["from before the migration"]
    finally:
        store.close()


def test_two_processes_may_migrate_the_same_file_at_once(tmp_path):
    """``session_summary`` opens the file a live hub already holds.

    ``PRAGMA table_info`` and ``ALTER TABLE`` are separate statements with no
    lock between them, so the loser of the race sees ``duplicate column name``.
    Both connections are opened and their ``SCHEMA`` scripts run *before* the
    barrier: that is what leaves the barrier sitting on the two-statement race
    window itself, rather than on the much slower open that precedes it. With
    the barrier on the open instead, the threads de-synchronise and the race
    never fires — the test would then pass with or without the guard.
    """
    path = _old_db(tmp_path / "old.db")
    barrier = threading.Barrier(2)
    errors: list[BaseException] = []

    def migrate_it():
        db = sqlite3.connect(path, check_same_thread=False)
        db.executescript(SCHEMA)
        try:
            barrier.wait(timeout=10)
            _migrate(db)
        except BaseException as exc:  # noqa: BLE001 — re-raised by the assert below
            errors.append(exc)
        finally:
            db.close()

    threads = [threading.Thread(target=migrate_it) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert not errors, errors
    assert _columns(path).count("sender_id") == 1
    assert _columns(path).count("recipient_id") == 1

    store = Store(path)  # the file is still usable after the collision
    try:
        assert [e.text for e in store.history()] == ["from before the migration"]
    finally:
        store.close()
