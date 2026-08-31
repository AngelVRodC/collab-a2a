"""Release checks: useful when they work, harmless when they cannot."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from collab import __version__, update


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.delenv("COLLAB_NO_UPDATE_CHECK", raising=False)
    return tmp_path


@pytest.mark.parametrize("candidate,current,expected", [
    ("1.2.0", "1.1.0", True),
    ("v1.2.0", "1.2.0", False),
    ("1.10.0", "1.9.0", True),
    ("1.0.1", "1.0.0", True),
    ("1.0.0", "1.0.1", False),
    ("2.0.0", "1.99.99", True),
])
def test_version_comparison(candidate, current, expected):
    assert update.is_newer(candidate, current) is expected


def test_a_newer_release_is_reported(monkeypatch):
    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"tag_name": "v99.0.0"}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    info = update.check(force=True)
    assert info.available is True
    assert info.latest == "99.0.0"
    assert info.current == __version__


def test_the_same_release_is_not_an_update(monkeypatch):
    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"tag_name": f"v{__version__}"}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: R())
    assert update.check(force=True).available is False


def test_being_offline_is_not_an_error(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(httpx, "get", boom)
    info = update.check(force=True)
    assert info.available is False
    assert info.error, "the reason is recorded, not raised"


def test_the_answer_is_cached(monkeypatch):
    calls = {"n": 0}

    class R:
        status_code = 200

        @staticmethod
        def json():
            return {"tag_name": "v99.0.0"}

    def counted(*a, **k):
        calls["n"] += 1
        return R()

    monkeypatch.setattr(httpx, "get", counted)
    update.check(force=True)
    update.check()
    update.check()
    assert calls["n"] == 1, "starting sessions all day should cost one request"


def test_a_cached_answer_about_an_older_build_is_re_evaluated(isolated_config):
    """Upgrading must not leave a stale 'update available' behind."""
    update.cache_path().parent.mkdir(parents=True, exist_ok=True)
    update.cache_path().write_text(json.dumps({
        "current": "0.0.1", "latest": __version__,
        "available": True, "checked_at": time.time(),
    }))
    info = update.read_cache()
    assert info.current == __version__
    assert info.available is False


def test_the_check_can_be_disabled(monkeypatch):
    monkeypatch.setenv("COLLAB_NO_UPDATE_CHECK", "1")

    def boom(*a, **k):
        raise AssertionError("must not reach the network when disabled")

    monkeypatch.setattr(httpx, "get", boom)
    assert update.check(force=True).error == "disabled"


def test_a_non_interactive_caller_is_told_not_asked(monkeypatch, capsys):
    """An agent's session must not block on a question nobody will see."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr(update, "apply_update",
                        lambda: (_ for _ in ()).throw(AssertionError("no install")))

    info = update.UpdateInfo(current="1.0.0", latest="2.0.0", available=True)
    assert update.prompt_and_maybe_update(info) is False
    assert "2.0.0 is available" in capsys.readouterr().out
