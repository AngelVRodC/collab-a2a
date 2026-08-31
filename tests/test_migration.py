"""Opening a database written by an older collab.

Sessions are meant to be resumable — a conversation from last month should
still open. `CREATE TABLE IF NOT EXISTS` leaves an existing table exactly as it
was, so when identity moved from display names to ids, every older session
became unreadable: the first read of `participants` raised and took `collab
sessions`, resume, and the hub itself down with it.
"""

from __future__ import annotations

import json
import sqlite3
import time

import pytest

from collab.server.store import Store, token_hash

#: The shape collab wrote before identity became an id.
OLD_SCHEMA = """
CREATE TABLE events (
    seq       INTEGER PRIMARY KEY AUTOINCREMENT,
    kind      TEXT NOT NULL,
    room      TEXT,
    sender    TEXT NOT NULL,
    recipient TEXT,
    ts        TEXT NOT NULL,
    payload   TEXT NOT NULL
);
CREATE TABLE participants (
    name       TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    is_host    INTEGER NOT NULL DEFAULT 0,
    joined_at  REAL NOT NULL,
    last_seen  REAL NOT NULL,
    revoked    INTEGER NOT NULL DEFAULT 0,
    meta       TEXT NOT NULL DEFAULT '{}'
);
"""


@pytest.fixture()
def old_db(tmp_path):
    """A session as an older collab would have left it."""
    path = tmp_path / "hub.db"
    con = sqlite3.connect(path)
    con.executescript(OLD_SCHEMA)
    now = time.time()
    for name, host, token in (("jarvis", 1, "aaa"), ("cortana", 0, "bbb")):
        con.execute(
            "INSERT INTO participants (name, token_hash, is_host, joined_at,"
            " last_seen, meta) VALUES (?,?,?,?,?,?)",
            # Tokens were only ever stored hashed, then as now.
            (name, token_hash(token), host, now, now, "{}"),
        )
    for i, (sender, recipient) in enumerate(
            [("jarvis", None), ("cortana", None), ("jarvis", "cortana")]):
        payload = json.dumps({"collab": "v1", "kind": "chat", "from": sender,
                              "text": f"m{i}", "room": "general", "seq": i + 1,
                              "ts": "2026-08-01T10:00:00Z"})
        con.execute(
            "INSERT INTO events (kind, room, sender, recipient, ts, payload)"
            " VALUES (?,?,?,?,?,?)",
            ("chat", "general", sender, recipient, "2026-08-01T10:00:00Z", payload),
        )
    con.commit()
    con.close()
    return path


def test_an_old_session_opens_instead_of_raising(old_db):
    """This is the crash: reading participants blew up on a missing column."""
    store = Store(old_db)
    try:
        assert {p.name for p in store.participants()} == {"jarvis", "cortana"}
    finally:
        store.close()


def test_everyone_gets_an_id(old_db):
    store = Store(old_db)
    try:
        people = store.participants()
        assert all(p.id.startswith("p_") for p in people)
        assert len({p.id for p in people}) == 2, "and they are distinct"
    finally:
        store.close()


def test_names_still_resolve_after_the_migration(old_db):
    """Someone holding the old name must still be reachable."""
    store = Store(old_db)
    try:
        jarvis = next(p for p in store.participants() if p.name == "jarvis")
        assert store.resolve_name("jarvis") == jarvis.id
    finally:
        store.close()


def test_the_history_is_all_still_there(old_db):
    store = Store(old_db)
    try:
        assert len(store.history(limit=100)) == 3
        assert store.max_seq() == 3
    finally:
        store.close()


def test_old_direct_messages_stay_private(old_db):
    """Events carried names only; visibility now compares ids.

    Without backfilling them, a message addressed to someone becomes visible
    to nobody — or, worse, to everybody.
    """
    store = Store(old_db)
    try:
        people = {p.name: p.id for p in store.participants()}
        jarvis_sees = [e.text for e in store.since(0, viewer=people["jarvis"])]
        cortana_sees = [e.text for e in store.since(0, viewer=people["cortana"])]

        assert "m2" in jarvis_sees, "the sender sees their own direct message"
        assert "m2" in cortana_sees, "and so does the recipient"

        store.add_participant("dave", "ccc")
        dave = next(p for p in store.participants() if p.name == "dave")
        assert "m2" not in [e.text for e in store.since(0, viewer=dave.id)]
    finally:
        store.close()


def test_the_token_still_works(old_db):
    """Nobody should have to rejoin because collab was upgraded."""
    store = Store(old_db)
    try:
        assert store.participant_for_token("aaa").name == "jarvis"
    finally:
        store.close()


def test_migrating_twice_changes_nothing(old_db):
    first = Store(old_db)
    try:
        before = {p.name: p.id for p in first.participants()}
    finally:
        first.close()

    second = Store(old_db)
    try:
        assert {p.name: p.id for p in second.participants()} == before
    finally:
        second.close()


def test_a_current_database_is_untouched(tmp_path):
    store = Store(tmp_path / "new.db")
    try:
        person = store.add_participant("alice", "tok", is_host=True)
        original = person.id
    finally:
        store.close()

    reopened = Store(tmp_path / "new.db")
    try:
        assert reopened.participants()[0].id == original
    finally:
        reopened.close()
