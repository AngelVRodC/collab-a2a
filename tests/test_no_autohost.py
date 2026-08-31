"""A failed join must not read as an invitation to host.

`collab host` always succeeds, so it is the most attractive next command for an
agent that just failed to connect — and it connects you to nobody. It opens a
different session while the other side waits in theirs, and both agents then
report success.
"""

from __future__ import annotations

import os

import pytest

from collab import peers
from collab.cli import main
from collab.protocol import Envelope
from collab.server.session import create_session, stop_session
from collab.server.store import Store


@pytest.fixture(autouse=True)
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / ".collab"))
    monkeypatch.setenv("COLLAB_PEERS_DIR", str(tmp_path / "peers"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _stopped_session(messages: int = 2):
    cfg = create_session("alice", 9000)
    store = Store(cfg.db_path)
    try:
        for i in range(messages):
            store.append(Envelope(kind="chat", text=f"m{i}", sender="alice",
                                  room="general"))
    finally:
        store.close()
    stop_session(cfg)
    return cfg


def test_nothing_found_warns_against_hosting(capsys):
    code = main(["join", "--local"])
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 1
    assert "do not host as a fallback" in out.lower()
    assert "different" in out, "say what hosting would actually do"


def test_a_named_session_that_is_not_here_warns_too(capsys):
    code = main(["join", "--local", "s_nosuch"])
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert code == 1
    assert "do not host as a fallback" in out.lower()


def test_a_stopped_session_is_the_one_case_where_host_is_right(capsys):
    """Resuming this repo's own session is not the same as hosting a new one —
    but it is still the user's decision, not an automatic retry."""
    cfg = _stopped_session()
    code = main(["join", "--local", cfg.session_id])
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 1
    assert "brings it back" in out
    assert "the user's call" in out
    assert "do not host as a fallback" not in out.lower(), \
        "here hosting is right; do not tell them not to"


def test_a_guest_only_registry_warns(capsys, monkeypatch):
    """A guest holds no invite, so there is nothing here to join."""
    peers.announce(session_id="s_theirs", name="bob", role="guest",
                   url="http://127.0.0.1:9000", repo="/elsewhere",
                   home="/elsewhere", invite="", pid=os.getpid())

    code = main(["join", "--local", "s_theirs"])
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert code == 1
    assert "no invite" in out
    assert "do not host as a fallback" in out.lower()


def test_an_unreachable_hub_warns(capsys, monkeypatch):
    """The remote case: the link is fine, the hub is not answering."""
    code = main(["join", "http://127.0.0.1:9/#nope"])
    captured = capsys.readouterr()
    out = captured.out + captured.err
    assert code == 1
    assert "do not host as a fallback" in out.lower()


def test_joining_never_starts_a_hub_itself(capsys):
    """The strongest form of the rule: the code does not do it either."""
    from collab.server.session import hosted_sessions

    main(["join", "--local"])
    main(["join", "--local", "s_nosuch"])
    main(["join", "http://127.0.0.1:9/#nope"])

    assert hosted_sessions() == [], "a failed join created a session"
