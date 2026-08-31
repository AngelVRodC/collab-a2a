"""The status line segment: never wrong, never slow, never fatal."""

from __future__ import annotations

import json
import socket
import time

import pytest

from collab.statusline import render as r


def _status(**kw):
    base = {"name": "bob", "host": "alice", "state": "live",
            "others_connected": 0, "unread": 0, "heartbeat": time.time()}
    return {**base, **kw}


def test_shows_you_the_host_and_the_count():
    out = r.render(_status(others_connected=3))
    assert "bob" in out and "alice" in out and "+3" in out


def test_host_line_does_not_repeat_the_name():
    out = r.render(_status(name="alice", host="alice", others_connected=2))
    assert "alice (host)" in out
    assert "alice → alice" not in out


def test_stale_heartbeat_downgrades_to_reconnecting():
    """A killed daemon leaves 'live' behind, so age is the only honest signal."""
    out = r.render(_status(state="live", heartbeat=time.time() - 20))
    assert "reconnecting" in out


def test_very_stale_heartbeat_reads_as_offline():
    out = r.render(_status(state="live", heartbeat=time.time() - 300))
    assert "offline" in out


def test_no_session_renders_nothing():
    assert r.render({}) == ""


def test_no_color_strips_ansi(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    out = r.render(_status(others_connected=1))
    assert "\033[" not in out


def test_narrow_width_drops_the_label_not_the_facts():
    out = r.render(_status(others_connected=3), width=18)
    assert "bob" in out and "alice" in out
    assert "collab" not in out


def test_unread_badge_appears():
    assert "✉2" in r.render(_status(unread=2))


def test_render_never_opens_a_socket(monkeypatch):
    """It runs on every status line refresh; a network call could stall it."""
    def explode(*a, **k):
        raise AssertionError("the status line must not touch the network")

    monkeypatch.setattr(socket.socket, "connect", explode)
    r.render(_status(others_connected=1))


def test_main_is_never_fatal(monkeypatch, capsys):
    """A broken collab must not break someone else's status line."""
    monkeypatch.setattr(r, "render", lambda **kw: 1 / 0)
    assert r.main([]) == 0
    assert capsys.readouterr().out == ""


def test_json_output_is_machine_readable(monkeypatch, capsys):
    monkeypatch.setattr(r, "status_payload", lambda cwd: {"active": True, "state": "live"})
    r.main(["--json"])
    assert json.loads(capsys.readouterr().out)["state"] == "live"
