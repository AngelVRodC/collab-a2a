"""Sessions that already carry the stuck names.

The fix to `add_participant` only helps rows written after it. A session that
has been running for weeks holds the rows that caused the 500, and would go on
refusing those names for as long as it lives — so opening it repairs it.
"""

from __future__ import annotations

import sqlite3

import pytest

from collab.server.store import Store


def _old_session(path, names_and_revoked):
    """A database in the current shape, with rows the old code left behind."""
    store = Store(path)
    try:
        for i, (name, revoked) in enumerate(names_and_revoked):
            person = store.add_participant(name, f"tok-{i}")
            if revoked:
                store.revoke(person.id)
    finally:
        store.close()


def test_opening_it_frees_the_names_of_removed_participants(tmp_path):
    db = tmp_path / "hub.db"
    _old_session(db, [("alice", False), ("bob", True), ("carol", True)])

    store = Store(db)                      # the migration runs here
    try:
        names = {r["name"] for r in
                 store._db.execute("SELECT name FROM participants").fetchall()}
        assert "alice" in names, "a live participant keeps their name"
        assert "bob" not in names and "carol" not in names
        assert sum(1 for n in names if "~" in n) == 2

        # And the point of all of it: they can be used again.
        assert store.add_participant("bob", "fresh").name == "bob"
    finally:
        store.close()


def test_it_is_idempotent(tmp_path):
    """Opening a session twice must not retire the retirement."""
    db = tmp_path / "hub.db"
    _old_session(db, [("bob", True)])

    for _ in range(3):
        store = Store(db)
        store.close()

    store = Store(db)
    try:
        names = [r["name"] for r in
                 store._db.execute("SELECT name FROM participants").fetchall()]
        assert len(names) == 1
        assert names[0].count("~") == 1, f"retired repeatedly: {names[0]}"
    finally:
        store.close()


def test_a_healthy_session_is_left_alone(tmp_path):
    db = tmp_path / "hub.db"
    _old_session(db, [("alice", False), ("bob", False)])

    store = Store(db)
    try:
        names = {r["name"] for r in
                 store._db.execute("SELECT name FROM participants").fetchall()}
        assert names == {"alice", "bob"}
    finally:
        store.close()


def test_the_conversation_survives_the_repair(tmp_path):
    """Renaming a row must not lose what that participant said."""
    from collab.protocol import Envelope

    db = tmp_path / "hub.db"
    store = Store(db)
    bob = store.add_participant("bob", "tok")
    store.append(Envelope(kind="chat", text="something bob said", sender="bob",
                          sender_id=bob.id, room="general"))
    store.revoke(bob.id)
    store.close()

    store = Store(db)
    try:
        texts = [e.text for e in store.since(0)]
        assert "something bob said" in texts
    finally:
        store.close()
