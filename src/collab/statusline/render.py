"""The status line segment.

Correctness rule for this module: it reads one local file and nothing else.
Claude Code cancels an in-flight status line script when the next update
triggers, so a segment that touched the network could stall the whole line.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from ..config import SessionProfile
from ..client.daemon import read_status

#: Beyond this, the daemon's heartbeat is old enough that it is not just quiet.
STALE_AFTER = 10.0
DEAD_AFTER = 45.0

RESET = "\033[0m"
COLORS = {
    "live": "\033[32m",         # green
    "reconnecting": "\033[33m", # yellow
    "offline": "\033[31m",      # red
    "dim": "\033[2m",
    "label": "\033[36m",        # cyan
}

GLYPHS = {"live": "●", "reconnecting": "◐", "offline": "○"}


def _use_color() -> bool:
    return not os.environ.get("NO_COLOR")


def _paint(text: str, color: str) -> str:
    if not _use_color():
        return text
    return f"{COLORS.get(color, '')}{text}{RESET}"


def _effective_state(status: dict[str, Any]) -> str:
    """Judge liveness from the heartbeat age, not from what the daemon claimed.

    A daemon that was killed leaves 'live' behind in its last status write, so
    the timestamp is the only trustworthy signal.
    """
    raw = status.get("state", "offline")
    age = time.time() - float(status.get("heartbeat") or 0)
    if raw in ("stopped", "unauthorized"):
        return "offline"
    if age > DEAD_AFTER:
        return "offline"
    if raw == "live" and age > STALE_AFTER:
        return "reconnecting"
    if raw == "live":
        return "live"
    return "reconnecting" if raw in ("reconnecting", "starting") else "offline"


def cwd_from_session_json(raw: str) -> Path | None:
    """Pull the working directory out of Claude Code's status line payload.

    State lives in a per-repo .collab/, and the status line script's own cwd is
    not guaranteed to be the session's, so we take it from the JSON when given.
    """
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    workspace = data.get("workspace") or {}
    for candidate in (workspace.get("current_dir"), data.get("cwd"),
                      workspace.get("project_dir")):
        if candidate:
            path = Path(str(candidate))
            if path.is_dir():
                return path
    return None


def render(status: dict[str, Any] | None = None, *, width: int | None = None,
           cwd: Path | None = None) -> str:
    """Build the segment.  Returns '' when there is nothing worth showing."""
    if status is None:
        profile = SessionProfile.current(cwd)
        if profile is None:
            return ""
        status = read_status(profile)
        if not status:
            return ""
    if not status:
        return ""

    state = _effective_state(status)
    name = status.get("name") or "?"
    host = status.get("host") or "?"
    others = int(status.get("others_connected") or 0)
    unread = int(status.get("unread") or 0)

    glyph = _paint(GLYPHS[state], state)
    label = _paint("collab", "label")

    who = f"{name} → {host}" if name != host else f"{name} (host)"

    if state == "live":
        tail = _paint(f"+{others}", "dim") if others else _paint("alone", "dim")
    elif state == "reconnecting":
        tail = _paint("reconnecting…", "reconnecting")
    else:
        tail = _paint("offline", "offline")

    parts = [glyph, label, who, tail]
    if unread:
        parts.append(_paint(f"✉{unread}", "live"))
    line = "  ".join(parts)

    limit = width or _terminal_width()
    if limit and _visible_len(line) > limit:
        # Drop the decorative half before truncating anything informative.
        line = "  ".join([glyph, who, tail])
    return line


def _visible_len(s: str) -> int:
    out, in_esc = 0, False
    for ch in s:
        if in_esc:
            if ch == "m":
                in_esc = False
        elif ch == "\033":
            in_esc = True
        else:
            out += 1
    return out


def _terminal_width() -> int | None:
    # Claude Code captures the script's output, so COLUMNS is the only source.
    try:
        return int(os.environ["COLUMNS"])
    except (KeyError, ValueError):
        return None


def status_payload(cwd: Path | None = None) -> dict[str, Any]:
    """The same facts as the rendered line, for hosts that format their own."""
    profile = SessionProfile.current(cwd)
    if profile is None:
        return {"active": False}
    status = read_status(profile)
    if not status:
        return {"active": False}
    return {
        "active": True,
        "state": _effective_state(status),
        "name": status.get("name"),
        "host": status.get("host"),
        "is_host": bool(status.get("is_host")),
        "others_connected": status.get("others_connected", 0),
        "unread": status.get("unread", 0),
        "session_id": status.get("session_id"),
    }


def main(argv: list[str] | None = None) -> int:
    """Universal entry point: one short line on stdout, always exit 0.

    Never fails loudly and never touches the network, so any host can call it
    as often as it likes without risking a stalled or broken status bar.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="collab statusline render", add_help=True)
    parser.add_argument("--plain", action="store_true", help="no ANSI colour")
    parser.add_argument("--json", action="store_true", help="structured output instead of a line")
    parser.add_argument("--cwd", help="resolve the session for this directory")
    parser.add_argument("--width", type=int, help="truncate to this many columns")
    args = parser.parse_args(argv if argv is not None else [])

    cwd = Path(args.cwd) if args.cwd else None
    if cwd is None:
        # Claude Code hands us the session JSON; other hosts pass nothing. A
        # host that leaves stdin open must not stall or blank the segment.
        try:
            if not sys.stdin.isatty():
                cwd = cwd_from_session_json(sys.stdin.read())
        except Exception:
            cwd = None

    try:
        if args.plain:
            os.environ["NO_COLOR"] = "1"
        if args.json:
            sys.stdout.write(json.dumps(status_payload(cwd)))
            return 0
        line = render(cwd=cwd, width=args.width)
    except Exception:
        return 0
    if line:
        sys.stdout.write(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
