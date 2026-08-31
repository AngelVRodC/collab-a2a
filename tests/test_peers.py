"""Local discovery, and knowing who shares your machine."""

from __future__ import annotations

import os

import pytest

from collab import peers


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_PEERS_DIR", str(tmp_path / "peers"))
    return tmp_path


def _announce(session_id="s_1", name="alice", role="host", invite="INV", **kw):
    return peers.announce(session_id=session_id, name=name, role=role,
                          url="http://127.0.0.1:5000", repo="/repo/api",
                          home="/repo/api/.collab", invite=invite, **kw)


def test_a_live_session_is_discoverable():
    _announce()
    found = peers.discover()
    assert [p.name for p in found] == ["alice"]
    assert found[0].joinable
    assert found[0].join_url().endswith("#INV")


def test_the_record_is_owner_only():
    """It carries a live invite, so it is not for other users to read."""
    path = _announce()
    assert oct(path.stat().st_mode)[-3:] == "600"


def test_a_guest_is_listed_but_not_joinable():
    """A guest holds no invite, so it has nothing to hand out."""
    _announce(name="bob", role="guest", invite="")
    peer = peers.discover()[0]
    assert peer.alive and not peer.joinable


def test_two_participants_in_one_session_do_not_overwrite_each_other(monkeypatch):
    """A host and a guest on one machine are the common case."""
    real_pid = os.getpid()
    _announce(session_id="s_1", name="alice", role="host")
    # A second process in the same session, still alive (same real pid).
    monkeypatch.setattr(peers.os, "getpid", lambda: real_pid + 1)
    _announce(session_id="s_1", name="bob", role="guest", invite="")
    monkeypatch.setattr(peers.os, "getpid", lambda: real_pid)

    names = sorted(p.name for p in peers.discover(include_stale=True))
    assert names == ["alice", "bob"]


def test_a_dead_record_is_ignored_and_pruned(registry):
    _announce()
    record = next((registry / "peers").glob("*.json"))
    data = record.read_text().replace(f'"pid": {os.getpid()}', '"pid": 999999')
    record.write_text(data)

    assert peers.discover() == []
    assert not record.exists(), "a record for a dead process is litter"


def test_find_prefers_something_joinable():
    """Asking to join what is here must not return our own guest record."""
    _announce(session_id="s_guest", name="bob", role="guest", invite="")
    _announce(session_id="s_host", name="alice", role="host", invite="INV")

    assert peers.find("").session_id == "s_host"


def test_find_by_session_name_or_repo():
    _announce(session_id="s_abc", name="alice")
    assert peers.find("s_abc").name == "alice"
    assert peers.find("alice").session_id == "s_abc"
    assert peers.find("api").session_id == "s_abc"
    assert peers.find("nothing-like-this") is None


def test_withdrawing_removes_the_record():
    _announce()
    peers.withdraw("s_1")
    assert peers.discover() == []


def test_co_location_is_recognised_by_fingerprint():
    mine = peers.identity()
    assert peers.same_machine(mine) is True
    assert peers.same_machine({"machine_id": "m_somewhere_else"}) is False
    assert peers.same_machine({}) is False, "no fingerprint is not a match"


def test_the_fingerprint_does_not_leak_the_machine_name():
    """It travels to participants on other machines, so it is hashed."""
    ident = peers.identity()
    assert ident["machine_id"].startswith("m_")
    assert ident["machine"] not in ident["machine_id"]
    assert ident["user"] not in ident["machine_id"]
