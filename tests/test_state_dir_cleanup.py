"""A per-agent directory leaves when its agent does.

One directory per agent per repo, left behind after every session, is litter in
someone's checkout — and unlike the default one it holds nothing they chose to
keep. The exception is a directory that *hosts* a session: that holds the only
copy of a conversation, and stopping is not losing.
"""

from __future__ import annotations

import os

import pytest

from collab import lockfile
from collab.cli import main
from collab.config import SessionProfile
from collab.protocol import Envelope
from collab.server.session import create_session
from collab.server.store import Store


@pytest.fixture(autouse=True)
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_PEERS_DIR", str(tmp_path / "peers"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COLLAB_HOME", raising=False)
    return tmp_path


def _guest_in(home, monkeypatch):
    """A guest's state directory: a profile, a lock, no hosted session."""
    monkeypatch.setenv("COLLAB_HOME", str(home))
    home.mkdir(parents=True, exist_ok=True)
    profile = SessionProfile(session_id="s_1", url="http://127.0.0.1:9",
                             name="bob", host_name="alice", token="t",
                             home=str(home))
    profile.dir.mkdir(parents=True, exist_ok=True)
    profile.save()
    (home / "current").write_text("s_1")
    lockfile.acquire(lockfile.Lock(name="bob", session_id="s_1", role="guest",
                                   listener_pid=os.getpid()), home)
    return profile


def test_a_guests_directory_goes_when_it_leaves(repo, monkeypatch, capsys):
    home = repo / ".collab-bob"
    _guest_in(home, monkeypatch)

    main(["kill"])
    out = capsys.readouterr().out

    assert not home.exists(), "nothing of bob's is left in this repo"
    assert ".collab-bob" in out, "and it says so"


def test_the_repos_own_directory_is_never_removed(repo, monkeypatch, capsys):
    home = repo / ".collab"
    _guest_in(home, monkeypatch)

    main(["kill"])
    assert home.exists(), "the default belongs to the repo, not to one agent"


def test_a_directory_hosting_a_session_is_kept(repo, monkeypatch, capsys):
    """It holds the only copy of that conversation."""
    home = repo / ".collab-bob"
    monkeypatch.setenv("COLLAB_HOME", str(home))
    cfg = create_session("bob", 9000)
    cfg.pid = os.getpid()
    cfg.save()
    store = Store(cfg.db_path)
    store.append(Envelope(kind="chat", text="worth keeping", sender="bob",
                          room="general"))
    store.close()
    SessionProfile(session_id=cfg.session_id, url="u", name="bob",
                   host_name="bob", token="t", is_host=True,
                   home=str(home)).save()
    (home / "current").write_text(cfg.session_id)

    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    main(["kill"])
    out = capsys.readouterr().out

    assert home.exists(), "stopping is not losing"
    assert "kept" in out
    assert cfg.db_path.exists()


def test_purging_a_hosted_session_takes_the_directory_too(repo, monkeypatch, capsys):
    home = repo / ".collab-bob"
    monkeypatch.setenv("COLLAB_HOME", str(home))
    cfg = create_session("bob", 9000)
    cfg.pid = os.getpid()
    cfg.save()
    SessionProfile(session_id=cfg.session_id, url="u", name="bob",
                   host_name="bob", token="t", is_host=True,
                   home=str(home)).save()
    (home / "current").write_text(cfg.session_id)

    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    main(["kill", "--purge", "--yes"])

    assert not home.exists(), "nothing was kept, so nothing should remain"
