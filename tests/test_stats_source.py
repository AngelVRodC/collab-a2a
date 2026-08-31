"""Keeping usage current without relying on an agent to remember.

Figures nobody refreshes are worse than none: they read as fact while being
hours old. A command the daemon runs on a timer needs no diligence at all.
"""

from __future__ import annotations

import pytest

from collab import config


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_CONFIG", str(tmp_path / "config.json"))


def test_no_source_by_default():
    assert config.stats_source() == ("", config.DEFAULT_STATS_INTERVAL)


def test_a_source_is_remembered():
    assert config.set_stats_source("my-usage", 60) == ("my-usage", 60)
    assert config.stats_source() == ("my-usage", 60)


def test_a_source_can_be_cleared():
    config.set_stats_source("my-usage")
    assert config.set_stats_source("")[0] == ""


def test_the_interval_has_a_floor():
    """Shelling out every second would cost more than the figures are worth."""
    assert config.set_stats_source("x", 1)[1] == 15


def test_clearing_the_command_keeps_the_interval():
    config.set_stats_source("x", 300)
    assert config.set_stats_source("")[1] == 300


def test_a_nonsense_interval_falls_back():
    config.save_config({"stats_command": "x", "stats_interval": "often"})
    assert config.stats_source()[1] == config.DEFAULT_STATS_INTERVAL
