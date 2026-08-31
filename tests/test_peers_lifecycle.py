"""Writing, reading and cleaning the machine registry.

The registry is how an agent in another checkout finds this session without a
link, and a host's record carries a live invite. Both failure directions are
bad: a record that outlives its process offers a hub that is not listening, and
a record that expires under a hub that *is* listening hides a session that is
perfectly joinable.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from collab import peers
from collab.server.session import create_session


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_PEERS_DIR", str(tmp_path / "peers"))
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / ".collab"))
    return tmp_path / "peers"


def _announce(session_id="s_x", *, pid=None, role="host", invite="inv", age=0.0):
    path = peers.announce(session_id=session_id, name="alice", role=role,
                          url="http://127.0.0.1:9000", repo="/repo",
                          home="/repo/.collab", invite=invite,
                          pid=pid or os.getpid())
    if age:
        data = json.loads(path.read_text())
        data["updated_at"] = time.time() - age
        path.write_text(json.dumps(data))
    return path


# --- writing -----------------------------------------------------------------

def test_a_record_is_written_atomically_and_kept_private(registry):
    path = _announce()
    assert path.exists()
    assert oct(path.stat().st_mode)[-3:] == "600", "it carries a live invite"
    assert not list(registry.glob("*.tmp")), "no half-written file left behind"


def test_refreshing_updates_the_record_in_place(registry):
    first = _announce(age=40)
    before = json.loads(first.read_text())["updated_at"]
    second = _announce()
    assert second == first, "one record per participant, not one per beat"
    assert json.loads(second.read_text())["updated_at"] > before


def test_two_participants_of_one_session_get_their_own_records(registry):
    _announce(pid=os.getpid(), role="host")
    _announce(pid=os.getppid(), role="guest", invite="")
    assert len(list(registry.glob("*.json"))) == 2
    assert {p.role for p in peers.discover()} == {"host", "guest"}


# --- reading -----------------------------------------------------------------

def test_a_live_record_is_found_and_joinable():
    _announce()
    found = peers.find("s_x")
    assert found is not None and found.joinable


def test_a_record_whose_process_is_gone_is_not_offered(monkeypatch):
    _announce(pid=999999)
    monkeypatch.setattr(os, "kill", _gone)
    assert peers.discover() == []


def test_a_record_nobody_has_refreshed_is_not_offered():
    """Expiry is what protects against a crashed process whose pid got reused."""
    _announce(age=peers.STALE_AFTER + 5)
    assert peers.discover() == []


def test_a_hub_that_keeps_saying_so_stays_visible():
    """The fix for the above: the living have to keep announcing themselves."""
    _announce(age=peers.STALE_AFTER + 5)
    assert peers.discover() == []
    _announce()                       # one heartbeat
    assert [p.session_id for p in peers.discover()] == ["s_x"]


# --- cleaning ----------------------------------------------------------------

def test_reading_removes_records_of_dead_processes(registry, monkeypatch):
    _announce(pid=999999)
    monkeypatch.setattr(os, "kill", _gone)
    peers.discover()
    assert list(registry.glob("*.json")) == [], "the file itself is gone"


def test_reading_without_pruning_leaves_the_file(registry, monkeypatch):
    _announce(pid=999999)
    monkeypatch.setattr(os, "kill", _gone)
    peers.discover(prune=False)
    assert len(list(registry.glob("*.json"))) == 1


def test_an_unreadable_record_is_eventually_cleaned(registry):
    """Truncated by a crash mid-write, or written by a shape we no longer know."""
    bad = registry / "s_broken-1.json"
    registry.mkdir(parents=True, exist_ok=True)
    bad.write_text('{"session_id": "s_broken", "pid": ')
    old = time.time() - (peers.STALE_AFTER + 60)
    os.utime(bad, (old, old))

    peers.discover()
    assert not bad.exists()


def test_a_record_being_written_right_now_is_not_deleted(registry):
    """Briefly unreadable is not the same as garbage — that race is ours."""
    registry.mkdir(parents=True, exist_ok=True)
    half = registry / "s_new-1.json"
    half.write_text('{"session_id": "s_new"')      # mtime is now

    peers.discover()
    assert half.exists(), "give the writer its moment"


def test_withdrawing_removes_only_our_own(registry):
    _announce("s_ours", pid=os.getpid())
    _announce("s_theirs", pid=os.getppid())

    peers.withdraw("s_ours", os.getpid())
    left = {p.session_id for p in peers.discover()}
    assert left == {"s_theirs"}


def test_withdrawing_without_a_pid_clears_what_is_dead(registry, monkeypatch):
    """A stopped hub should not linger because the caller forgot its pid."""
    _announce("s_gone", pid=999999)
    monkeypatch.setattr(os, "kill", _gone)
    peers.withdraw("s_gone")
    assert list(registry.glob("*.json")) == []


def test_stopping_a_session_takes_its_record_with_it(monkeypatch):
    cfg = create_session("alice", 9000)
    cfg.pid = os.getpid()
    cfg.save()
    _announce(cfg.session_id, pid=cfg.pid)

    from collab.server.session import stop_session
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    stop_session(cfg)

    assert peers.find(cfg.session_id) is None


# --- the hub keeps itself alive ---------------------------------------------

def test_the_hub_refreshes_its_own_record():
    """The listener was the only thing beating, so stopping it hid the hub."""
    from collab.hub_main import RegistryHeartbeat

    cfg = create_session("alice", 9000)
    cfg.pid = os.getpid()
    cfg.save()

    beat = RegistryHeartbeat(cfg)
    beat.beat()
    found = peers.find(cfg.session_id)
    assert found is not None and found.joinable
    assert found.invite == cfg.invite, "the invite is what makes it joinable"


def test_the_heartbeat_picks_up_a_rotated_invite():
    """Resuming rotates the invite; a record still offering the old one is a
    join that fails with a credential error."""
    from collab.hub_main import RegistryHeartbeat

    cfg = create_session("alice", 9000)
    cfg.pid = os.getpid()
    cfg.save()
    beat = RegistryHeartbeat(cfg)
    beat.beat()

    cfg.invite = "a-brand-new-invite"
    cfg.save()
    beat.beat()

    assert peers.find(cfg.session_id).invite == "a-brand-new-invite"


def test_the_heartbeat_withdraws_when_the_hub_stops():
    from collab.hub_main import RegistryHeartbeat

    cfg = create_session("alice", 9000)
    cfg.pid = os.getpid()
    cfg.save()
    beat = RegistryHeartbeat(cfg)
    beat.beat()
    assert peers.find(cfg.session_id) is not None

    beat.stop()
    assert peers.find(cfg.session_id) is None


def test_the_refresh_interval_leaves_room_to_miss_a_beat():
    from collab.hub_main import REGISTRY_REFRESH

    assert REGISTRY_REFRESH * 2 < peers.STALE_AFTER, \
        "one missed beat must not expire a live session"


def _gone(pid, sig):
    raise ProcessLookupError
