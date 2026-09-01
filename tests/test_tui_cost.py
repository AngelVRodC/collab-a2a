"""What the conversation pane costs to look at.

Laying the conversation out is linear in the whole history — every message
wrapped, folded and framed — and it was being done again on every redraw: four
times a second while nothing happened, and once per keystroke while somebody
scrolled. Measured at 300 messages that was two seconds a frame, most of it
`git` subprocesses resolving the reader's own name once PER MESSAGE.

Nothing in the layout depends on where you are scrolled, so these tests pin the
two halves of the fix: the rows are built when an input to them changes, and
the name behind them is not re-derived from the filesystem for each line.
"""

from __future__ import annotations

from collab.client import tui as T
from collab.config import SessionProfile
from collab.protocol import KIND_CHAT, Envelope


def Msg(seq):
    return Envelope(seq=seq, ts="2026-08-31T10:00:00+00:00", kind=KIND_CHAT,
                    sender="alice", body={"text": f"message {seq}"})


class FakeModel:
    def __init__(self, n=50):
        self.profile = SessionProfile(session_id="s", url="u", name="me",
                                      host_name="host", token="t", home="/tmp")
        self.events = [Msg(i + 1) for i in range(n)]
        self.snapshot = {}
        self.status = {}

    def more_above(self):
        return False

    def load_older(self, count=200):
        return 0


def _counting(monkeypatch):
    """conversation_rows, with a tally of how often it actually ran."""
    calls = []
    real = T.conversation_rows

    def counted(events, width, me, expanded=None, **kw):
        calls.append(width)
        return real(events, width, me, expanded, **kw)

    monkeypatch.setattr(T, "conversation_rows", counted)
    return calls


def test_scrolling_does_not_lay_the_conversation_out_again(monkeypatch):
    calls = _counting(monkeypatch)
    tui = T.Tui(FakeModel())

    tui._conversation(80)
    for _ in range(20):
        tui.chat.scroll(3)
        tui._conversation(80)

    assert len(calls) == 1, "twenty wheel notches, one layout"


def test_a_new_message_does_lay_it_out_again(monkeypatch):
    calls = _counting(monkeypatch)
    tui = T.Tui(FakeModel())

    tui._conversation(80)
    tui.model.events.append(Msg(999))
    tui._conversation(80)

    assert len(calls) == 2


def test_a_resize_does_too(monkeypatch):
    """The rows are wrapped to a width; a different width is different rows."""
    calls = _counting(monkeypatch)
    tui = T.Tui(FakeModel())

    tui._conversation(80)
    tui._conversation(120)

    assert calls == [80, 120]


def test_unfolding_a_message_does_too(monkeypatch):
    calls = _counting(monkeypatch)
    tui = T.Tui(FakeModel())

    tui._conversation(80)
    tui.expanded.add(3)
    tui._conversation(80)

    assert len(calls) == 2


def test_the_readers_own_name_is_not_resolved_once_per_message(monkeypatch):
    """`resolve_name` runs `git rev-parse`, `git config user.name` and walks the
    state directories. Per message, per frame, that was 900 forks a redraw."""
    T._OWN_NAME.clear()
    calls = []

    def counted():
        calls.append(1)
        return "me"

    monkeypatch.setattr(T, "resolve_name", counted)
    for _ in range(500):
        T.my_names("me")

    assert len(calls) == 1


def test_but_it_is_re_read_soon_enough_to_notice_a_rename(monkeypatch):
    """`collab name` in another terminal has to reach an open viewer."""
    T._OWN_NAME.clear()
    monkeypatch.setattr(T, "resolve_name", lambda: "before")
    assert "before" in T.my_names("me")

    monkeypatch.setattr(T, "resolve_name", lambda: "after")
    monkeypatch.setattr(T.time, "monotonic",
                        lambda: T._OWN_NAME["at"] + T.OWN_NAME_TTL + 0.01)
    assert "after" in T.my_names("me")
