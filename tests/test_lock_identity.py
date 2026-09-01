"""The lock answers "who am I, and where is my state".

An agent has no memory between commands, so anything it must know about itself
has to be readable somewhere. Its name it usually knows; its participant id —
the thing that survives a rename and that routing actually uses — and the
folders holding its session, it does not.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from collab import lockfile
from collab.cli import _take_lock, main
from collab.config import SessionProfile


@pytest.fixture(autouse=True)
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_PEERS_DIR", str(tmp_path / "peers"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _profile(home: Path, name="bob", pid="p_abc123") -> SessionProfile:
    profile = SessionProfile(session_id="s_1", url="http://127.0.0.1:9",
                             name=name, host_name="alice", token="t",
                             participant_id=pid, home=str(home))
    profile.dir.mkdir(parents=True, exist_ok=True)
    profile.save()
    return profile


def test_it_records_the_identity_and_the_places(repo, monkeypatch):
    home = repo / ".collab-bob"
    monkeypatch.setenv("COLLAB_HOME", str(home))
    profile = _profile(home)

    _take_lock(profile, role="guest")
    lock = lockfile.read(home)

    assert lock.name == "bob"
    assert lock.participant_id == "p_abc123", "the id routing uses"
    assert Path(lock.state_dir) == home
    assert Path(lock.session_dir) == profile.dir
    assert Path(lock.profile_path) == profile.dir / "profile.json"


def test_the_default_folder_is_recorded_too(repo, monkeypatch):
    """It used to be left blank when it was the usual one, which meant the
    answer to "where is my state" was blank exactly when nothing had gone
    wrong yet."""
    home = repo / ".collab"
    monkeypatch.setenv("COLLAB_HOME", str(home))
    profile = _profile(home, name="alice")

    _take_lock(profile, role="host")
    assert Path(lockfile.read(home).state_dir) == home


def test_identity_reads_back_as_a_whole(repo, monkeypatch):
    home = repo / ".collab"
    monkeypatch.setenv("COLLAB_HOME", str(home))
    _take_lock(_profile(home), role="guest")

    identity = lockfile.read(home).identity()
    assert identity["name"] == "bob"
    assert identity["id"] == "p_abc123"
    assert identity["session"] == "s_1"
    assert identity["profile"].endswith("profile.json")


def test_the_command_prints_it(repo, monkeypatch, capsys):
    home = repo / ".collab"
    monkeypatch.setenv("COLLAB_HOME", str(home))
    _take_lock(_profile(home), role="guest")

    main(["lock"])
    out = capsys.readouterr().out

    assert "bob" in out
    assert "p_abc123" in out, "an agent asking who it is must be told"
    assert "profile.json" in out


def test_json_carries_it_for_a_program(repo, monkeypatch, capsys):
    home = repo / ".collab"
    monkeypatch.setenv("COLLAB_HOME", str(home))
    _take_lock(_profile(home), role="guest")

    main(["lock", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert data["participant_id"] == "p_abc123"
    assert data["session_dir"].endswith("sessions/s_1")
    assert data["held"] is False or data["held"] is True


def test_a_lock_written_before_these_fields_still_reads(repo, monkeypatch):
    """Sessions running through an upgrade must not break on the older shape."""
    home = repo / ".collab"
    home.mkdir(parents=True)
    (home / "agent.lock").write_text(json.dumps({
        "name": "alice", "session_id": "s_old", "hub_pid": os.getpid(),
    }))

    lock = lockfile.holder(home)
    assert lock is not None and lock.name == "alice"
    assert lock.participant_id == "" and lock.session_dir == ""
