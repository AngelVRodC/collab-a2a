"""The pane holds a window over the conversation, not the conversation.

What is loaded is what the pane costs to draw, so it is bounded: it opens on a
handful of messages and slides — a page at a time, at either end — over a log
that keeps everything. The point of these tests is that «bounded» never means
«lost»: every message is still reachable, in both directions, and the pane says
which way there is more.
"""

from __future__ import annotations

import json

import pytest

from collab.client import tui as T
from collab.client.inbox import Inbox
from collab.config import SessionProfile
from collab.protocol import KIND_CHAT, Envelope, now_iso


def _seqs(events):
    return [e.seq for e in events]


@pytest.fixture()
def session(tmp_path):
    """A session directory with a hundred messages already in the log."""
    home = tmp_path / "collab"
    directory = home / "sessions" / "s"
    directory.mkdir(parents=True)
    inbox = Inbox(directory)
    for i in range(100):
        inbox.record(Envelope(seq=i + 1, ts=now_iso(), kind=KIND_CHAT,
                              sender="other", body={"text": f"message {i + 1}"}))
    inbox.close()
    (directory / "snapshot.json").write_text(json.dumps({"participants": []}))
    return SessionProfile(session_id="s", url="u", name="me", host_name="host",
                          token="t", home=str(home))


def _model(session, limit=T.OPEN_WITH):
    model = T.Model(profile=session)
    model.load_initial(limit=limit)
    return model


def test_it_opens_on_the_last_few_and_says_there_is_more(session):
    model = _model(session)
    assert _seqs(model.events) == [96, 97, 98, 99, 100]
    assert model.more_above()


def test_scrolling_back_slides_the_window_without_growing_it(session):
    model = _model(session)
    for _ in range(10):
        model.load_older()

    assert len(model.events) <= T.WINDOW, "the window is a window"
    assert model.events[0].seq < 96, "and it moved back"


def test_every_message_is_reachable_going_back(session):
    """A bounded window must not mean a bounded conversation."""
    model = _model(session)
    seen = set(_seqs(model.events))
    while model.load_older():
        seen.update(_seqs(model.events))

    assert seen == set(range(1, 101))
    assert not model.more_above(), "and it stops at the beginning"


def test_and_going_forward_again(session):
    model = _model(session)
    model.load_start()
    assert _seqs(model.events)[0] == 1

    seen = set(_seqs(model.events))
    while model.load_newer():
        seen.update(_seqs(model.events))

    assert seen == set(range(1, 101))
    assert model.pending() == 0, "it ends at what was said last"


def test_the_end_is_one_step_away_however_far_back_you_went(session):
    model = _model(session)
    model.load_start()

    model.load_tail()
    assert _seqs(model.events)[-1] == 100
    assert len(model.events) == T.WINDOW


def test_what_arrived_below_the_window_is_counted_not_lost(session):
    model = _model(session)
    model.load_start()

    assert model.pending() == 100 - len(model.events)


def test_a_frozen_window_does_not_move_under_the_reader(session):
    """Scrolled back, an arriving message must not slide the window: fifty in
    means fifty out, and the paragraph being read goes with them."""
    model = _model(session)
    before = _seqs(model.events)
    inbox = Inbox(session.dir)
    inbox.record(Envelope(seq=101, ts=now_iso(), kind=KIND_CHAT,
                          sender="other", body={"text": "new"}))

    assert model.poll_events(follow=False) == 0
    assert _seqs(model.events) == before
    assert model.pending() == 1, "counted, though"


def test_following_again_lands_on_what_is_being_said_now(session):
    model = _model(session)
    model.load_start()                       # away in the distant past
    inbox = Inbox(session.dir)
    inbox.record(Envelope(seq=101, ts=now_iso(), kind=KIND_CHAT,
                          sender="other", body={"text": "new"}))

    model.poll_events(follow=True)
    assert _seqs(model.events)[-1] == 101
    assert len(model.events) <= T.WINDOW
    # And not the beginning of the conversation with the newest bolted on.
    assert _seqs(model.events) == list(range(52, 102))


def test_live_messages_keep_the_window_the_same_size(session):
    model = _model(session, limit=T.WINDOW)
    inbox = Inbox(session.dir)
    for seq in range(101, 121):
        inbox.record(Envelope(seq=seq, ts=now_iso(), kind=KIND_CHAT,
                              sender="other", body={"text": "live"}))

    model.poll_events(follow=True)
    assert len(model.events) == T.WINDOW
    assert _seqs(model.events)[-1] == 120


def test_a_log_that_was_replaced_is_reloaded_not_spliced(session):
    """A state directory rebuilt under a running viewer: reading on from the
    old byte offset would join the middle of one conversation to another."""
    model = _model(session)
    (session.dir / "inbox.jsonl").write_text("")

    model.poll_events(follow=True)
    assert model._seen == 0
    assert model.events, "and it still shows the conversation it can read"
