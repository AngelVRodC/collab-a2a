"""The watch pane must not die in your hands.

Dragging a tmux border narrow put one write into the last cell of the pane,
`addnwstr() returned ERR` came out through `curses.wrapper`, and `collab watch`
caught it, said one line about it and dropped to the plain scrolling
transcript — the full-screen view gone until you restarted it. It looked like
a crash because it was one.

So this drives the real viewer through a real pty and resizes it to sizes
nobody would choose on purpose. The assertion is that it is still the curses
view afterwards, not that the process is technically alive: falling back to the
plain renderer keeps a process and loses the pane.
"""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import time

import pytest

from collab.client.inbox import Inbox
from collab.protocol import KIND_CHAT, Envelope, now_iso

FALLBACK = b"could not start the full view"

#: Sizes to drag through: one column, one row, and the awkward widths either
#: side of the «window too small» threshold.
SIZES = [(40, 120), (3, 100), (1, 100), (40, 20), (40, 5), (40, 1), (1, 1),
         (2, 2), (8, 24), (5, 24), (4, 23), (9, 25), (40, 120)]


def _session(home, messages=40):
    session = home / "sessions" / "s_test"
    session.mkdir(parents=True)
    (session / "profile.json").write_text(json.dumps({
        "session_id": "s_test", "url": "http://127.0.0.1:9/", "name": "me",
        "host_name": "host", "token": "t", "home": str(home),
        "participant_id": "p_me"}))
    (home / "current").write_text("s_test\n")
    (session / "snapshot.json").write_text(json.dumps({
        "title": "test", "participants": [
            {"id": "p_me", "name": "me", "connected": True},
            {"id": "p_o", "name": "other", "connected": True}]}))
    (session / "status.json").write_text(json.dumps(
        {"state": "live", "name": "me", "host": "host"}))
    (session / "daemon.pid").write_text(str(os.getpid()))

    inbox = Inbox(session)
    for i in range(messages):
        inbox.record(Envelope(seq=i + 1, ts=now_iso(), kind=KIND_CHAT,
                              sender="other" if i % 2 else "me",
                              body={"text": f"message number {i} " + "word " * 40}))
    inbox.close()
    return session


def _setsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs a pty")
def test_the_pane_survives_being_dragged_to_silly_sizes(tmp_path):
    home = tmp_path / "collab"
    home.mkdir()
    _session(home)

    pid, fd = pty.fork()
    if pid == 0:                                    # the viewer
        os.environ.update(TERM="xterm-256color", COLLAB_HOME=str(home),
                          COLLAB_CONFIG=str(tmp_path / "config.json"),
                          COLLAB_PEERS_DIR=str(tmp_path / "peers"))
        os.execvp(sys.executable, [sys.executable, "-m", "collab.cli", "watch",
                                   "--session", "s_test"])

    seen = bytearray()

    def drain(seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if ready:
                try:
                    seen.extend(os.read(fd, 65536))
                except OSError:
                    return

    def running():
        try:
            return os.waitpid(pid, os.WNOHANG)[0] == 0
        except ChildProcessError:       # already reaped: it is gone
            return False

    try:
        _setsize(fd, 40, 120)
        drain(3.0)
        assert running(), "the viewer did not start"

        for rows, cols in SIZES:
            _setsize(fd, rows, cols)
            os.kill(pid, signal.SIGWINCH)
            os.write(fd, b"jkG")          # and keep asking it to draw
            drain(0.4)
            assert running(), f"the viewer exited at {rows}x{cols}"
            assert FALLBACK not in seen, \
                f"the full-screen view was lost at {rows}x{cols}"

        os.write(fd, b"q")
        deadline = time.time() + 5
        while running() and time.time() < deadline:
            drain(0.2)
        assert not running(), "q did not close the viewer"
    finally:
        if running():
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(fd)
