"""Rejoining under a name that was freed.

`participants.name` is UNIQUE, and a revoked row keeps occupying its name.
Every rule above the table said such a name was available again — the join
check, the suffixing loop, the documentation — so the row was the only thing
that disagreed, and it disagreed by raising IntegrityError from inside the
request. The agent saw:

    POST /ext/collab/v1/join failed (500): Internal Server Error

Found in a real hub log: 13 kicked participants, every one still holding its
name, so any of them rejoining under the name they had used before hit this.
"""

from __future__ import annotations

import sqlite3

import pytest

from collab.server.store import Store


@pytest.fixture()
def store(tmp_path):
    s = Store(tmp_path / "hub.db")
    yield s
    s.close()


_tokens = iter(range(10_000))


def _join(store, name):
    # A real join mints a fresh token every time, and token_hash is UNIQUE too.
    return store.add_participant(name, f"token-{name}-{next(_tokens)}")


def test_a_kicked_name_can_be_taken_again(store):
    """The case from the log, end to end."""
    bob = _join(store, "bob")
    store.revoke(bob.id)

    again = _join(store, "bob")
    assert again.name == "bob", "no suffix: the name really was free"
    assert again.id != bob.id, "a new participant, not the old one revived"


def test_the_old_holder_keeps_a_readable_trace(store):
    bob = _join(store, "bob")
    store.revoke(bob.id)
    _join(store, "bob")

    names = {r["name"] for r in
             store._db.execute("SELECT name FROM participants").fetchall()}
    retired = next(n for n in names if n.startswith("bob~"))
    assert retired.startswith("bob~"), "still recognisably bob's row"
    assert "bob" in names, "and the live one holds the plain name"


def test_two_kicked_holders_of_one_name_do_not_collide(store):
    """Retirement has to be unique too, or the second one raises instead."""
    first = _join(store, "bob")
    store.revoke(first.id)
    second = _join(store, "bob")
    store.revoke(second.id)

    third = _join(store, "bob")
    assert third.name == "bob"
    assert len(store._db.execute("SELECT id FROM participants").fetchall()) == 3


def test_a_live_holder_still_blocks_the_name(store):
    """Freeing revoked names must not free names somebody is using."""
    _join(store, "bob")
    other = _join(store, "bob")
    assert other.name == "bob-2", "suffixed, as before"


def test_name_taken_and_the_insert_agree(store):
    """They disagreed, which is the whole bug: one said free, the other raised."""
    bob = _join(store, "bob")
    assert store.name_taken("bob")

    store.revoke(bob.id)
    assert not store.name_taken("bob"), "the rule says it is free"
    assert _join(store, "bob").name == "bob", "and now the table agrees"


def test_renaming_onto_a_freed_name_works(store):
    """`rename` had the identical hazard."""
    bob = _join(store, "bob")
    store.revoke(bob.id)
    carol = _join(store, "carol")

    assert store.rename(carol.id, "bob") == "bob"


def test_renaming_onto_a_live_name_still_suffixes(store):
    _join(store, "bob")
    carol = _join(store, "carol")
    assert store.rename(carol.id, "bob") == "bob-2"


def test_a_join_never_raises_out_of_the_request(store, monkeypatch):
    """Whatever else is wrong, an agent must not be handed a 500.

    The backstop is a suffixed name — a small surprise, where a stack trace is
    a dead end.
    """
    calls = {"n": 0}

    class FailsOnce:
        """The connection, with the first participant insert refused."""

        def __init__(self, db):
            self._db = db

        def __getattr__(self, item):
            return getattr(self._db, item)

        def execute(self, sql, *args):
            if sql.lstrip().startswith("INSERT INTO participants") and not calls["n"]:
                calls["n"] += 1
                raise sqlite3.IntegrityError(
                    "UNIQUE constraint failed: participants.name")
            return self._db.execute(sql, *args)

    monkeypatch.setattr(store, "_db", FailsOnce(store._db))
    person = store.add_participant("bob", "tok")

    assert person.name.startswith("bob-"), "renamed rather than raised"
    assert calls["n"] == 1


def test_the_freed_name_routes_to_whoever_holds_it_now(store):
    """A direct message to "bob" must reach the bob who is here."""
    old = _join(store, "bob")
    store.revoke(old.id)
    new = _join(store, "bob")

    assert store.resolve_name("bob") == new.id
