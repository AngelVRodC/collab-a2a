"""The lock as the CLI uses it: taken, honoured, released, and questioned."""

from __future__ import annotations

import os

import pytest

from collab import lockfile
from collab.cli import main
from collab.config import SessionProfile
from collab.server.session import create_session, stop_session


@pytest.fixture(autouse=True)
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / ".collab"))
    monkeypatch.setenv("COLLAB_PEERS_DIR", str(tmp_path / "peers"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _held(**kw):
    fields = dict(name="alice", session_id="s_1", role="host",
                  url="http://127.0.0.1:9", hub_pid=os.getpid())
    fields.update(kw)
    return lockfile.acquire(lockfile.Lock(**fields))


# --- what `collab lock` shows ------------------------------------------------

def test_it_reports_an_empty_repo(capsys):
    assert main(["lock"]) == 0
    assert "none" in capsys.readouterr().out


def test_it_names_the_holder(capsys):
    _held(state_dir="/repo/.collab-bob")
    main(["lock"])
    out = capsys.readouterr().out

    assert "alice" in out and "s_1" in out and ".collab-bob" in out
    assert "alive" in out


def test_json_says_whether_it_is_held(capsys):
    import json

    _held()
    main(["lock", "--json"])
    assert json.loads(capsys.readouterr().out)["held"] is True


# --- clearing ----------------------------------------------------------------

def test_clearing_a_live_lock_is_refused(capsys):
    """It exists to stop two agents sharing one state; removing it on a whim
    is removing the protection."""
    _held()
    code = main(["lock", "clear"])
    captured = capsys.readouterr()

    assert code == 1
    assert "still has live processes" in captured.out + captured.err
    assert lockfile.read() is not None, "and it is still there"


def test_force_clears_it(capsys):
    _held()
    assert main(["lock", "clear", "--force"]) == 0
    assert lockfile.read() is None


def test_a_stale_lock_clears_without_force(capsys, monkeypatch):
    _held(hub_pid=999999)
    monkeypatch.setattr(os, "kill", _gone)

    assert main(["lock", "clear"]) == 0
    assert lockfile.read() is None


# --- it is released when the session ends -----------------------------------

def test_killing_the_session_drops_the_lock(monkeypatch, capsys):
    cfg = create_session("alice", 9000)
    cfg.pid = os.getpid()
    cfg.save()
    _held(session_id=cfg.session_id)
    SessionProfile(session_id=cfg.session_id, url="u", name="alice",
                   host_name="alice", token="t", is_host=True,
                   home=cfg.home).save()
    (cfg.dir.parent.parent / "current").write_text(cfg.session_id)

    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    main(["kill"])

    assert lockfile.read() is None, "leaving means leaving"


def test_another_sessions_lock_is_not_ours_to_drop(monkeypatch):
    """Killing our session must not clear a lock somebody else holds."""
    cfg = create_session("alice", 9000)
    cfg.pid = os.getpid()
    cfg.save()
    _held(session_id="s_someone_else")
    SessionProfile(session_id=cfg.session_id, url="u", name="alice",
                   host_name="alice", token="t", is_host=True,
                   home=cfg.home).save()
    (cfg.dir.parent.parent / "current").write_text(cfg.session_id)

    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    main(["kill"])

    assert lockfile.read() is not None
    assert lockfile.read().session_id == "s_someone_else"


# --- it decides which state directory we use --------------------------------

def test_a_held_lock_sends_us_to_our_own_directory(monkeypatch, repo):
    from collab.config import base_home, resolve_home

    monkeypatch.delenv("COLLAB_HOME", raising=False)
    assert resolve_home("bob") == base_home(), "free: use the default"

    _held()
    assert resolve_home("bob") == base_home().parent / ".collab-bob"


def test_a_stale_lock_leaves_the_default_free(monkeypatch, repo):
    from collab.config import base_home, resolve_home

    monkeypatch.delenv("COLLAB_HOME", raising=False)
    _held(hub_pid=999999)
    monkeypatch.setattr(os, "kill", _gone)
    assert resolve_home("bob") == base_home(), "a dead claim is no claim"


def _gone(pid, sig):
    raise ProcessLookupError
