"""Scrolling the roster.

The roster always *could* be scrolled — tab to it, then the arrow keys. But the
first thing anyone does is turn the wheel, and nothing was listening for it, so
the pane read as frozen. Measured in a real terminal: ncurses grants the mouse
mask and delivers KEY_MOUSE, and without `mousemask` those events never come.
"""

from __future__ import annotations

import curses

import pytest

from collab.client.tui import Pane, Tui, WHEEL_LINES
from collab.config import SessionProfile


class FakeModel:
    """Enough of a Model for the parts of Tui that do not draw."""

    def __init__(self):
        self.profile = SessionProfile(session_id="s", url="u", name="me",
                                      host_name="host", token="t", home="/tmp")
        self.events = []
        self.snapshot = {}
        self.status = {}


def _tui(view="both", chat_top=8):
    tui = Tui(FakeModel(), view=view)
    tui._chat_top = chat_top
    tui.roster.rows, tui.roster.total = 4, 40
    tui.chat.rows, tui.chat.total = 10, 200
    return tui


def _wheel(monkeypatch, tui, *, y, state):
    monkeypatch.setattr(curses, "getmouse", lambda: (0, 10, y, 0, state))
    return tui.handle(curses.KEY_MOUSE)


# --- which pane the wheel is over -------------------------------------------

def test_the_wheel_scrolls_the_pane_it_is_over(monkeypatch):
    tui = _tui()
    before = tui.roster.offset

    chat_before = tui.chat.offset
    _wheel(monkeypatch, tui, y=4, state=curses.BUTTON5_PRESSED)
    assert tui.roster.offset == before + WHEEL_LINES
    assert tui.chat.offset == chat_before, "chat untouched"


def test_over_the_conversation_it_scrolls_the_conversation(monkeypatch):
    tui = _tui()
    tui.chat.offset = 50
    tui.chat.follow = False
    roster_before = tui.roster.offset

    _wheel(monkeypatch, tui, y=20, state=curses.BUTTON5_PRESSED)
    assert tui.chat.offset == 53
    assert tui.roster.offset == roster_before


def test_wheel_up_goes_back(monkeypatch):
    tui = _tui()
    tui.roster.offset = 10
    _wheel(monkeypatch, tui, y=4, state=curses.BUTTON4_PRESSED)
    assert tui.roster.offset == 10 - WHEEL_LINES


def test_scrolling_a_pane_focuses_it(monkeypatch):
    """So the keys you reach for next go where you were just looking."""
    tui = _tui()
    assert tui.focus == "chat"
    _wheel(monkeypatch, tui, y=4, state=curses.BUTTON5_PRESSED)
    assert tui.focus == "roster"


def test_a_click_is_not_a_scroll(monkeypatch):
    tui = _tui()
    before = tui.roster.offset
    _wheel(monkeypatch, tui, y=4, state=curses.BUTTON1_PRESSED)
    assert tui.roster.offset == before


def test_a_mouse_event_that_cannot_be_read_is_survivable(monkeypatch):
    """getmouse raises if the queue moved on; that must not end the viewer."""
    tui = _tui()

    def boom():
        raise curses.error("no mouse event")

    monkeypatch.setattr(curses, "getmouse", boom)
    assert tui.handle(curses.KEY_MOUSE) is True


def test_in_a_single_pane_view_the_wheel_always_hits_that_pane(monkeypatch):
    tui = _tui(view="roster")
    _wheel(monkeypatch, tui, y=99, state=curses.BUTTON5_PRESSED)
    assert tui.roster.offset == WHEEL_LINES


# --- keys that reach the roster without taking focus ------------------------

def test_bracket_keys_scroll_the_roster_from_the_conversation():
    tui = _tui()
    assert tui.focus == "chat"

    tui.handle(ord("]"))
    assert tui.roster.offset == 1
    assert tui.focus == "chat", "you did not have to leave the conversation"

    tui.handle(ord("["))
    assert tui.roster.offset == 0


def test_tab_still_works():
    tui = _tui()
    tui.handle(ord("\t"))
    assert tui.focus == "roster"
    tui.handle(curses.KEY_DOWN)
    assert tui.roster.offset == 1


# --- the roster cannot be squeezed out of existence -------------------------

@pytest.mark.parametrize("height", [8, 10, 12, 20, 40])
def test_the_roster_keeps_at_least_one_visible_row(height):
    """At zero visible rows it renders nothing, and a pane you cannot see is a
    pane you cannot scroll."""
    from collab.client.tui import MIN_ROSTER_ROWS, ROSTER_SHARE

    body_height = height - 3
    roster_h = max(int(body_height * ROSTER_SHARE), MIN_ROSTER_ROWS)
    roster_h = min(roster_h, max(body_height - 4, 2))
    assert roster_h - 1 >= 1, f"roster invisible at height {height}"


def test_a_pane_that_fits_everything_reports_nothing_to_scroll():
    pane = Pane(rows=10, total=4)
    pane.settle()
    assert pane.offset == 0
