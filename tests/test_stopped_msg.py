"""What you are told when the session is here but stopped.

Codex asked for a session by id, was told "no session here matches", checked
`discover`, was told "nothing running here", and concluded the data was gone
and the host had to restart it. Every word was true and the conclusion was
wrong: the session was sitting in the repo with 442 messages in it.
"""

from __future__ import annotations

import pytest

from collab.server.session import create_session, stop_session
from collab.server.store import Store
from collab.protocol import Envelope


@pytest.fixture(autouse=True)
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / ".collab"))
    monkeypatch.setenv("COLLAB_PEERS_DIR", str(tmp_path / "peers"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _stopped_session(messages: int = 3):
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


def test_discover_says_what_is_kept_here(capsys):
    from collab.cli import main

    cfg = _stopped_session()
    main(["discover"])
    out = capsys.readouterr().out

    assert "nothing running here" in out
    assert cfg.session_id in out, "it is right here — say so"
    assert "3 messages" in out
    assert "collab host" in out


def test_joining_a_stopped_session_points_at_the_way_back(capsys):
    from collab.cli import main

    cfg = _stopped_session()
    code = main(["join", "--local", cfg.session_id])
    out = capsys.readouterr().out

    assert code == 1, "it genuinely cannot be joined while it is down"
    assert "on disk, stopped" in out
    assert "3 messages" in out
    assert "brings it back" in out
    assert "the data is kept" in out, "the fear is losing it; answer that"


def test_nothing_here_still_reads_as_nothing(capsys):
    """No invented reassurance when the repo really is empty."""
    from collab.cli import main

    main(["discover"])
    out = capsys.readouterr().out
    assert "nothing running here" in out
    assert "stopped, but kept" not in out
    assert "collab join <url>#<invite>" in out


def test_a_running_session_is_not_reported_as_stopped(capsys, monkeypatch):
    """The live one must not also show up in the 'stopped' list."""
    from collab import peers
    from collab.cli import main
    import os

    cfg = create_session("alice", 9000)
    peers.announce(session_id=cfg.session_id, name="alice", role="host",
                   url="http://127.0.0.1:9000", repo=str(cfg.home),
                   home=str(cfg.home), invite="inv", pid=os.getpid())

    main(["discover"])
    out = capsys.readouterr().out
    assert "stopped" not in out


def test_a_stopped_session_stops_advertising_itself(monkeypatch):
    """The pid check is a safety net, not the mechanism.

    A hub takes a moment to shut down and ``os.kill(pid, 0)`` keeps succeeding
    all the way through it, so a registry entry outlives the socket it points
    at — and whoever accepts the offer in that window gets a bare "connection
    refused" rather than being told the session is down.
    """
    import os
    from collab import peers

    cfg = create_session("alice", 9000)
    cfg.pid = os.getpid()          # a pid that is unmistakably alive
    cfg.save()
    peers.announce(session_id=cfg.session_id, name="alice", role="host",
                   url=cfg.local_url, repo=str(cfg.home), home=str(cfg.home),
                   invite=cfg.invite, pid=cfg.pid)
    assert peers.find(cfg.session_id) is not None, "advertised while up"

    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    stop_session(cfg)

    assert peers.find(cfg.session_id) is None, \
        "a stopped session must not still be on offer"


def test_withdrawing_leaves_other_sessions_alone(monkeypatch):
    """Two sessions on one machine is the normal case, not the exception."""
    import os
    from collab import peers

    mine = create_session("alice", 9000)
    mine.pid = os.getpid()
    mine.save()
    for cfg in (mine,):
        peers.announce(session_id=cfg.session_id, name="alice", role="host",
                       url=cfg.local_url, repo=str(cfg.home),
                       home=str(cfg.home), invite=cfg.invite, pid=cfg.pid)
    peers.announce(session_id="s_theirs", name="bob", role="host",
                   url="http://127.0.0.1:9999", repo="/elsewhere",
                   home="/elsewhere", invite="inv", pid=os.getpid())

    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    stop_session(mine)

    assert peers.find("s_theirs") is not None, "not ours to withdraw"
