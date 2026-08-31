"""Resuming a session: the data comes back, the way in does not.

Closing a terminal should not throw away a conversation and a task board. But a
link shared days ago should not still open the door either, so an invite is
retired on resume even though everything else is kept.
"""

from __future__ import annotations

import pytest

from collab.server.session import (create_session, hosted_sessions,
                                   resume_session, session_summary)
from collab.server.store import Store


@pytest.fixture(autouse=True)
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / ".collab"))
    return tmp_path


def _with_content(cfg, messages=3):
    """Give a session some history and a task, as a day's work would."""
    from collab.protocol import Envelope

    store = Store(cfg.db_path)
    try:
        for i in range(messages):
            store.append(Envelope(kind="chat", text=f"m{i}", sender="alice",
                                  room="general"))
        store.upsert_task("T_1", title="migrate sessions", state="TASK_STATE_SUBMITTED",
                          owner=None, room="general", created_by="alice")
    finally:
        store.close()
    return cfg


def test_a_hosted_session_is_listed_afterwards():
    cfg = create_session("alice", 9000)
    assert [c.session_id for c in hosted_sessions()] == [cfg.session_id]


def test_a_joined_session_is_not_resumable(repo):
    """Only a host holds the database, so only a host can bring one back."""
    guest = repo / ".collab" / "sessions" / "s_guest"
    guest.mkdir(parents=True)
    (guest / "profile.json").write_text("{}")
    assert hosted_sessions() == []


def test_resuming_keeps_the_history_and_the_tasks():
    cfg = _with_content(create_session("alice", 9000))
    before = session_summary(cfg)

    resumed = resume_session(cfg, 9100)

    assert resumed.session_id == cfg.session_id
    after = session_summary(resumed)
    assert after["messages"] == before["messages"] == 3
    assert after["tasks"] == 1


def test_resuming_retires_the_old_invite():
    """A link shared days ago must not quietly still work."""
    cfg = create_session("alice", 9000)
    old_invite = cfg.invite

    resumed = resume_session(cfg, 9100)

    assert resumed.invite != old_invite, "the way in has to change"
    store = Store(resumed.db_path)
    try:
        assert store.consume_invite(old_invite)[0] is False
        assert store.consume_invite(resumed.invite)[0] is True
    finally:
        store.close()


def test_participants_keep_their_access_across_a_resume():
    """It is the open door that closes, not everyone already inside."""
    cfg = create_session("alice", 9000)
    store = Store(cfg.db_path)
    try:
        store.add_participant("bob", "bob-token")
    finally:
        store.close()

    resumed = resume_session(cfg, 9100)

    store = Store(resumed.db_path)
    try:
        assert store.participant_for_token("bob-token").name == "bob"
        assert store.participant_for_token(cfg.host_token).is_host
    finally:
        store.close()


def test_a_fresh_session_shares_nothing_with_the_old_one():
    first = _with_content(create_session("alice", 9000))
    second = create_session("alice", 9100)

    assert second.session_id != first.session_id
    assert second.invite != first.invite
    assert session_summary(second)["messages"] == 0
    assert session_summary(first)["messages"] == 3, "the old one is kept, not deleted"


def test_the_most_recent_session_is_offered_first():
    create_session("alice", 9000)
    newest = create_session("alice", 9100)
    assert hosted_sessions()[0].session_id == newest.session_id


def test_resuming_moves_the_port_and_drops_the_stale_url():
    cfg = create_session("alice", 9000)
    cfg.public_url = "https://old.ngrok-free.app"
    cfg.tunnel = "ngrok"
    cfg.save()

    resumed = resume_session(cfg, 9100)

    assert resumed.port == 9100
    assert resumed.public_url == "", "a dead tunnel's URL must not be re-advertised"
