"""A terminal UI for watching a collab session.

Two panes: the roster on top, the conversation below. Each scrolls on its own,
because the two answer different questions — *who is here and what are they
burning* versus *what was just said* — and you often want to hold one still
while reading the other.

Everything is read from files the daemon maintains, so the viewer never touches
the network and keeps working through a reconnect.
"""

from __future__ import annotations

import curses
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import SessionProfile
from ..protocol import (
    Envelope,
    local_clock,
    KIND_CHAT,
    KIND_FILE,
    KIND_HELLO,
    KIND_PRESENCE,
    KIND_TASK,
)
from .. import peers
from .daemon import DaemonPaths, is_running, read_status
from .inbox import Inbox

#: How much of the window the roster gets. The conversation is the thing you
#: read continuously, so it keeps the majority.
ROSTER_SHARE = 0.30
MIN_ROSTER_ROWS = 3
POLL_SECONDS = 0.25

# Colour pair ids.
C_TITLE = 1
C_DIM = 2
C_ONLINE = 3
C_OFFLINE = 4
C_ACCENT = 5
C_WARN = 6
C_SPEAKER_BASE = 10
SPEAKER_COLORS = (curses.COLOR_CYAN, curses.COLOR_MAGENTA, curses.COLOR_GREEN,
                  curses.COLOR_YELLOW, curses.COLOR_BLUE, curses.COLOR_RED)

KIND_MARK = {
    KIND_CHAT: " ",
    KIND_HELLO: "→",
    KIND_PRESENCE: "·",
    KIND_TASK: "◆",
    KIND_FILE: "▣",
}


def _speaker_pair(name: str) -> int:
    return C_SPEAKER_BASE + (sum(name.encode()) % len(SPEAKER_COLORS))


def _fmt_money(value: Any) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return ""


def _fmt_pct(value: Any, label: str) -> str:
    try:
        return f"{label} {float(value):.0f}%"
    except (TypeError, ValueError):
        return ""


def quota_text(stats: dict[str, Any]) -> str:
    """The headroom figures, which are what you weigh when splitting work."""
    parts = []
    if (q := _fmt_pct(stats.get("quota_five_hour"), "5h")):
        parts.append(q)
    if (q := _fmt_pct(stats.get("quota_seven_day"), "7d")):
        parts.append(q)
    return "quota " + " ".join(parts) if parts else ""


def stat_line(person: dict[str, Any]) -> str:
    """One line of whatever this agent chose to share about itself."""
    stats = person.get("stats") or {}
    bits: list[str] = []
    repo = person.get("repo")
    if repo:
        branch = person.get("branch")
        bits.append(f"{repo}/{branch}" if branch else repo)
    if person.get("machine"):
        bits.append(str(person["machine"]))
    if stats.get("model"):
        bits.append(str(stats["model"]))
    if (quota := quota_text(stats)):
        bits.append(quota)
    if (money := _fmt_money(stats.get("cost_usd"))):
        bits.append(money)
    if (ctx := _fmt_pct(stats.get("context_pct"), "ctx")):
        bits.append(ctx)
    return " · ".join(bits)


@dataclass
class Pane:
    """A scrollable region. Sticks to the bottom until you scroll away."""

    offset: int = 0
    follow: bool = True
    rows: int = 0
    total: int = 0

    def clamp(self) -> None:
        limit = max(self.total - self.rows, 0)
        self.offset = max(0, min(self.offset, limit))

    def scroll(self, delta: int) -> None:
        self.offset += delta
        self.follow = False
        self.clamp()
        if self.offset >= max(self.total - self.rows, 0):
            self.follow = True

    def to_end(self) -> None:
        self.follow = True
        self.offset = max(self.total - self.rows, 0)

    def to_start(self) -> None:
        self.follow = False
        self.offset = 0

    def settle(self) -> None:
        if self.follow:
            self.offset = max(self.total - self.rows, 0)
        self.clamp()


@dataclass
class Model:
    """Everything on screen, refreshed from the daemon's files."""

    profile: SessionProfile
    events: list[Envelope] = field(default_factory=list)
    snapshot: dict[str, Any] = field(default_factory=dict)
    status: dict[str, Any] = field(default_factory=dict)
    _seen: int = 0

    @property
    def paths(self) -> DaemonPaths:
        return DaemonPaths(self.profile.dir)

    def title(self) -> str:
        return (self.snapshot.get("title")
                or self.profile.session_id)

    def participants(self) -> list[dict[str, Any]]:
        return list(self.snapshot.get("participants") or [])

    def load_initial(self, limit: int = 500) -> None:
        inbox = Inbox(self.profile.dir)
        self.events = inbox.all_events(limit=limit)
        self._seen = self.paths.root.joinpath("inbox.jsonl").stat().st_size \
            if (self.paths.root / "inbox.jsonl").exists() else 0
        self.refresh_side()

    def refresh_side(self) -> None:
        try:
            self.snapshot = json.loads(self.paths.snapshot.read_text())
        except (OSError, ValueError):
            pass
        self.status = read_status(self.profile) or self.status

    def poll_events(self) -> int:
        """Read whatever has been appended since we last looked."""
        path = self.paths.root / "inbox.jsonl"
        try:
            size = path.stat().st_size
        except OSError:
            return 0
        if size <= self._seen:
            return 0
        added = 0
        try:
            with path.open("r", encoding="utf-8") as fh:
                fh.seek(self._seen)
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self.events.append(Envelope.from_dict(json.loads(line)))
                        added += 1
                    except ValueError:
                        continue
                self._seen = fh.tell()
        except OSError:
            return 0
        return added


def _wrap(text: str, width: int) -> list[str]:
    if width <= 0:
        return [text]
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            if line:
                out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line or not out:
        out.append(line)
    return out


@dataclass
class Row:
    """A rendered line, with the colour pair and attributes it needs."""

    text: str
    pair: int = 0
    attr: int = 0


def event_rows(env: Envelope, width: int, me: str) -> list[Row]:
    clock = local_clock(env.ts)
    mark = KIND_MARK.get(env.kind, " ")
    speaker = env.sender or "?"
    label = f"{speaker}{' (you)' if speaker == me else ''}"
    head = f"{clock} {label:>14.14} {mark} "
    indent = " " * len(head)
    body_width = max(width - len(head) - 1, 20)

    if env.kind == KIND_CHAT:
        where = f"→{env.to}" if env.to else f"#{env.room}"
        lines = _wrap(f"{where}  {env.text}", body_width)
    elif env.kind == KIND_HELLO:
        b = env.body
        where = ", ".join(x for x in (b.get("repo"), b.get("branch")) if x)
        detail = "joined" + (f" from {where}" if where else "")
        if b.get("focus"):
            detail += f" — {b['focus']}"
        lines = _wrap(detail, body_width)
    elif env.kind == KIND_PRESENCE:
        lines = _wrap(str(env.body.get("event", "")), body_width)
    elif env.kind == KIND_TASK:
        b = env.body
        state = str(b.get("state", "")).replace("TASK_STATE_", "").lower()
        owner = f" · {b['owner']}" if b.get("owner") else ""
        lines = _wrap(f"{b.get('action','')} {b.get('id','')} "
                      f"“{b.get('title','')}” [{state}]{owner}", body_width)
    elif env.kind == KIND_FILE:
        b = env.body
        if b.get("action") == "received":
            lines = _wrap(f"collected {b.get('name')} (deleted from host)", body_width)
        else:
            size = int(b.get("size") or 0)
            lines = _wrap(f"shared {b.get('name')} ({size / 1024:.0f} KB) · "
                          f"collab file get {b.get('id')}", body_width)
    else:
        lines = _wrap(env.text or str(env.body), body_width)

    pair = _speaker_pair(speaker)
    dim_kinds = (KIND_PRESENCE, KIND_HELLO)
    rows = [Row(head + lines[0], pair,
                curses.A_DIM if env.kind in dim_kinds else 0)]
    for extra in lines[1:]:
        rows.append(Row(indent + extra, pair,
                        curses.A_DIM if env.kind in dim_kinds else 0))
    return rows


def roster_rows(model: Model, width: int) -> list[Row]:
    rows: list[Row] = []
    me = model.profile.name
    for person in model.participants():
        online = person.get("connected")
        glyph = "●" if online else "○"
        name = person.get("name", "?")
        tags = []
        if person.get("is_host"):
            tags.append("host")
        if name == me:
            tags.append("you")
        elif peers.same_machine(person):
            tags.append("same machine")
        suffix = f" ({', '.join(tags)})" if tags else ""
        focus = person.get("focus") or ""
        head = f" {glyph} {name}{suffix}"
        if focus:
            pad = max(30 - len(head), 1)
            head += " " * pad + focus
        rows.append(Row(head[:width], C_ONLINE if online else C_OFFLINE,
                        curses.A_BOLD if online else curses.A_DIM))
        if (detail := stat_line(person)):
            rows.append(Row(f"     {detail}"[:width], C_DIM, curses.A_DIM))
    if not rows:
        rows.append(Row("  (waiting for the roster…)", C_DIM, curses.A_DIM))
    return rows


class Tui:
    """The viewer.

    ``view`` selects what this window shows: both panes, or just one of them.
    A single-pane view is what makes the tmux layout possible — two windows,
    each showing one half, with tmux doing the splitting so the user can resize
    and move them with the keys they already know.
    """

    def __init__(self, model: Model, view: str = "both") -> None:
        self.model = model
        self.view = view if view in ("both", "chat", "roster") else "both"
        # The roster reads from the top — following its tail would hide whoever
        # joined first, including yourself. Only the conversation tails.
        self.roster = Pane(follow=False)
        self.chat = Pane()
        self.focus = "roster" if self.view == "roster" else "chat"

    # -- drawing ------------------------------------------------------------

    def _hline(self, win, y: int, width: int, label: str) -> None:
        win.attron(curses.color_pair(C_DIM))
        win.hline(y, 0, curses.ACS_HLINE, width)
        win.attroff(curses.color_pair(C_DIM))
        if label:
            focused = (label.lower().startswith("participants") and self.focus == "roster") \
                or (label.lower().startswith("conversation") and self.focus == "chat")
            attr = curses.color_pair(C_ACCENT) | (curses.A_BOLD if focused else 0)
            text = f" {label} "
            win.addnstr(y, 2, text, max(width - 4, 0), attr)

    def draw(self, win) -> None:
        win.erase()
        height, width = win.getmaxyx()
        if height < 4 or width < 24:
            win.addnstr(0, 0, "window too small", max(width - 1, 1))
            win.refresh()
            return

        if self.view != "both":
            self._draw_single(win, height, width)
            win.refresh()
            return

        if height < 8:
            # Not enough room for two panes; show the conversation rather than
            # squeezing both into something unreadable.
            self.view, restore = "chat", True
            self._draw_single(win, height, width)
            self.view = "both" if restore else self.view
            win.refresh()
            return

        # --- title bar -----------------------------------------------------
        m = self.model
        state = str(m.status.get("state") or "?")
        state_pair = {"live": C_ONLINE, "reconnecting": C_WARN}.get(state, C_OFFLINE)
        title = m.title()
        left = f" {title} "
        # The host is its own host; saying so twice reads like a mistake.
        who = (f"{m.profile.name} (host)" if m.profile.name == m.profile.host_name
               else f"{m.profile.name} → {m.profile.host_name}")
        right = f" {who} "
        version = m.status.get("version") or ""

        win.attron(curses.color_pair(C_TITLE) | curses.A_BOLD)
        win.hline(0, 0, " ", width)
        win.addnstr(0, 0, left, max(width - 1, 0))
        win.attroff(curses.color_pair(C_TITLE) | curses.A_BOLD)
        tail = f"{right}"
        if version:
            tail += f" v{version} "
        win.addnstr(0, max(width - len(tail) - 1, 0), tail[:max(width - 1, 0)],
                    max(width - 1, 0), curses.color_pair(C_TITLE))
        badge = f" {state} "
        win.addnstr(1, 0, badge, max(width - 1, 0),
                    curses.color_pair(state_pair) | curses.A_BOLD)
        people = m.participants()
        online = sum(1 for p in people if p.get("connected"))
        summary = f"{online}/{len(people)} online"
        win.addnstr(1, len(badge) + 1, summary, max(width - len(badge) - 2, 0),
                    curses.color_pair(C_DIM))

        # --- geometry ------------------------------------------------------
        body_top = 2
        body_height = height - body_top - 1
        roster_h = max(int(body_height * ROSTER_SHARE), MIN_ROSTER_ROWS)
        roster_h = min(roster_h, body_height - 4)

        rows = roster_rows(self.model, width - 1)
        self.roster.rows = roster_h - 1
        self.roster.total = len(rows)
        self.roster.settle()
        hidden = max(len(rows) - self.roster.rows - self.roster.offset, 0)
        label = f"PARTICIPANTS ({len(people)})"
        if hidden or self.roster.offset:
            label += f" · {self.roster.offset + 1}-" \
                     f"{min(self.roster.offset + self.roster.rows, len(rows))}" \
                     f" of {len(rows)}"
        self._hline(win, body_top, width, label)
        for i in range(self.roster.rows):
            idx = self.roster.offset + i
            if idx >= len(rows):
                break
            r = rows[idx]
            win.addnstr(body_top + 1 + i, 0, r.text, width - 1,
                        curses.color_pair(r.pair) | r.attr)

        chat_top = body_top + roster_h
        self._hline(win, chat_top, width, "CONVERSATION")

        chat_rows: list[Row] = []
        for env in self.model.events:
            chat_rows.extend(event_rows(env, width - 1, self.model.profile.name))
        self.chat.rows = height - chat_top - 2
        self.chat.total = len(chat_rows)
        self.chat.settle()
        for i in range(self.chat.rows):
            idx = self.chat.offset + i
            if idx >= len(chat_rows):
                break
            r = chat_rows[idx]
            win.addnstr(chat_top + 1 + i, 0, r.text, width - 1,
                        curses.color_pair(r.pair) | r.attr)

        # --- help ----------------------------------------------------------
        hint = " tab: pane · ↑↓ pgup/pgdn: scroll · g/G: top/end · q: quit "
        if not self.chat.follow:
            hint = " ⏸ scrolled back — G to resume following ·" + hint
        win.addnstr(height - 1, 0, hint[:width - 1], width - 1,
                    curses.color_pair(C_DIM) | curses.A_DIM)
        win.refresh()

    # -- input --------------------------------------------------------------

    def handle(self, key: int) -> bool:
        """Returns False when the user asked to leave."""
        if self.view != "both":
            self.focus = "roster" if self.view == "roster" else "chat"
        pane = self.roster if self.focus == "roster" else self.chat
        # Deliberately not ESC: terminals send a bare ESC as the first byte of
        # every escape sequence — focus events, bracketed paste, cursor-position
        # replies — so quitting on it makes the view close itself at random.
        if key in (ord("q"), ord("Q")):
            return False
        if key == ord("\t"):
            self.focus = "roster" if self.focus == "chat" else "chat"
        elif key in (curses.KEY_UP, ord("k")):
            pane.scroll(-1)
        elif key in (curses.KEY_DOWN, ord("j")):
            pane.scroll(1)
        elif key == curses.KEY_PPAGE:
            pane.scroll(-max(pane.rows - 1, 1))
        elif key == curses.KEY_NPAGE:
            pane.scroll(max(pane.rows - 1, 1))
        elif key == ord("g"):
            pane.to_start()
        elif key == ord("G"):
            pane.to_end()
        return True

    # -- single-pane views ---------------------------------------------------

    def _draw_single(self, win, height: int, width: int) -> None:
        """One half, filling the window. tmux owns the split in this mode."""
        m = self.model
        people = m.participants()
        if self.view == "roster":
            rows = roster_rows(m, width - 1)
            pane, label = self.roster, f"PARTICIPANTS ({len(people)})"
        else:
            rows = []
            for env in m.events:
                rows.extend(event_rows(env, width - 1, m.profile.name))
            pane, label = self.chat, "CONVERSATION"

        state = str(m.status.get("state") or "?")
        state_pair = {"live": C_ONLINE, "reconnecting": C_WARN}.get(state, C_OFFLINE)
        head = f" {m.title()} · {label} "
        win.attron(curses.color_pair(C_TITLE) | curses.A_BOLD)
        win.hline(0, 0, " ", width)
        win.addnstr(0, 0, head, max(width - 1, 0))
        win.attroff(curses.color_pair(C_TITLE) | curses.A_BOLD)
        badge = f" {state} "
        win.addnstr(0, max(width - len(badge) - 1, 0), badge, max(width - 1, 0),
                    curses.color_pair(state_pair) | curses.A_BOLD)

        pane.rows = height - 2
        pane.total = len(rows)
        pane.settle()
        for i in range(pane.rows):
            idx = pane.offset + i
            if idx >= len(rows):
                break
            r = rows[idx]
            win.addnstr(1 + i, 0, r.text, width - 1,
                        curses.color_pair(r.pair) | r.attr)

        hint = " ↑↓ pgup/pgdn: scroll · g/G: top/end · q: quit "
        win.addnstr(height - 1, 0, hint[:width - 1], width - 1,
                    curses.color_pair(C_DIM) | curses.A_DIM)


def _init_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_TITLE, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(C_DIM, curses.COLOR_WHITE, -1)
    curses.init_pair(C_ONLINE, curses.COLOR_GREEN, -1)
    curses.init_pair(C_OFFLINE, curses.COLOR_RED, -1)
    curses.init_pair(C_ACCENT, curses.COLOR_CYAN, -1)
    curses.init_pair(C_WARN, curses.COLOR_YELLOW, -1)
    for i, colour in enumerate(SPEAKER_COLORS):
        curses.init_pair(C_SPEAKER_BASE + i, colour, -1)


def run(profile: SessionProfile, view: str = "both") -> int:
    model = Model(profile=profile)
    model.load_initial()
    tui = Tui(model, view=view)

    def loop(win) -> int:
        _init_colors()
        try:
            curses.curs_set(0)
        except curses.error:
            pass  # some terminals cannot hide the cursor
        win.nodelay(True)
        win.keypad(True)
        # Swallow the terminal's own replies rather than treating them as input.
        try:
            curses.set_escdelay(25)
        except (AttributeError, curses.error):
            pass
        last_side = 0.0
        while True:
            now = time.time()
            if now - last_side > 1.0:
                model.refresh_side()
                last_side = now
            model.poll_events()
            tui.draw(win)

            key = win.getch()
            if key == 27:
                # Drain the rest of the sequence so its tail is not read as
                # commands (an arrow key would otherwise scroll unbidden).
                while win.getch() != -1:
                    pass
                key = -1
            if key == curses.KEY_RESIZE:
                continue
            if key != -1 and not tui.handle(key):
                return 0
            time.sleep(POLL_SECONDS if key == -1 else 0.01)

    return curses.wrapper(loop)
