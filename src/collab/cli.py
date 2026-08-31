"""The collab command line.

Two commands do almost all the work.  ``collab host`` and ``collab join`` are
deliberately composite: each one leaves you connected, listening, announced,
and holding the context needed to say something useful — rather than handing
back a connection and a list of follow-up steps.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .client import onboard
from .client.daemon import (DaemonPaths, is_running, read_status,
                            stop as stop_daemon, stop_orphans)
from .client.hub_client import HubClient, HubError
from .client.inbox import Inbox
from .config import (
    SessionProfile,
    collab_home,
    ensure_home,
    resolve_name,
    set_default_name,
)
from .client.context import gather as ctx_gather
from .protocol import (DEFAULT_ROOM, MAX_FILE_BYTES, Envelope, KIND_CHAT,
                       KIND_HELLO)
from .server.session import HubConfig, create_session, join_line
from .server.tunnel import NO_NGROK_HELP, free_port, local_ip, ngrok_version

# --- output helpers ----------------------------------------------------------

def _tty() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty() else text


def ok(msg: str) -> None:
    print(f"{c('[ok]', '32')}   {msg}")


def warn(msg: str) -> None:
    print(f"{c('[warn]', '33')} {msg}")


def fail(msg: str) -> None:
    print(f"{c('[fail]', '31')} {msg}", file=sys.stderr)


def dim(msg: str) -> str:
    return c(msg, "2")


def heading(msg: str) -> None:
    print(f"\n{c(msg, '1')}")


def _warn_outside_venv() -> None:
    if sys.prefix == sys.base_prefix:
        warn("running outside a virtualenv — collab expects to live in .venv")


# --- shared pieces -------------------------------------------------------------

def _require_profile(args: argparse.Namespace) -> SessionProfile:
    profile = (
        SessionProfile.load(args.session) if getattr(args, "session", None)
        else SessionProfile.current()
    )
    if profile is None:
        raise SystemExit(
            f"no active collab session in {collab_home()}\n"
            "  start one with `collab host`, or join one with `collab join <url>#<invite>`"
        )
    return profile


def _client(profile: SessionProfile) -> HubClient:
    return HubClient(profile.url, profile.token)


def _monitor_hint(profile: SessionProfile, status: dict[str, Any]) -> None:
    """Tell the agent exactly how to start listening, with the real port filled in."""
    port = status.get("bridge_port") or profile.bridge_port
    exe = sys.argv[0]
    heading("To receive messages in real time, arm a Monitor on one of these:")
    print(f"  {c('command', '36')}   {exe} listen --follow")
    if port:
        print(f"  {c('ws', '36')}        ws://127.0.0.1:{port}/events")
    print(dim("  (either one delivers the same events; the daemon handles reconnects)"))


def _print_snapshot(snapshot: dict[str, Any], me: str) -> None:
    people = snapshot.get("participants", [])
    others = [p for p in people if p.get("name") != me]
    heading("Who's here")
    if not others:
        print(dim("  nobody else yet — you'll be notified the moment someone joins"))
    for p in people:
        mark = "*" if p["name"] == me else " "
        state = c("online", "32") if p.get("connected") else dim("offline")
        role = " (host)" if p.get("is_host") else ""
        focus = f" — {p['focus']}" if p.get("focus") else ""
        repo = f" [{p['repo']}{'/' + p['branch'] if p.get('branch') else ''}]" if p.get("repo") else ""
        print(f" {mark} {p['name']}{role}  {state}{repo}{focus}")

    if tasks := snapshot.get("tasks"):
        heading("Open tasks")
        for t in tasks:
            owner = t.get("owner") or dim("unclaimed")
            print(f"  {t['id']}  {t['title']}  [{_short_state(t['state'])}]  {owner}")

    if recent := snapshot.get("recent"):
        heading("Recent")
        for e in recent[-8:]:
            print("  " + Envelope.from_dict(e).render_line())


def _short_state(state: str) -> str:
    return state.replace("TASK_STATE_", "").lower()


# --- commands -----------------------------------------------------------------

def cmd_host(args: argparse.Namespace) -> int:
    _warn_outside_venv()
    ensure_home()
    name = resolve_name(args.name)
    port = args.port or free_port()
    cfg = create_session(name, port, bind=args.bind, domain=args.domain)

    env = {**os.environ, "COLLAB_HOME": cfg.home}
    if args.no_tunnel:
        env["COLLAB_NO_TUNNEL"] = "1"

    log = DaemonPaths(cfg.dir).root / "hub.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as fh:
        subprocess.Popen(
            [sys.executable, "-m", "collab.hub_main", cfg.session_id],
            stdout=fh, stderr=fh, stdin=subprocess.DEVNULL,
            start_new_session=True, env=env,
        )

    ok(f"session {c(cfg.session_id, '36')} starting as {c(name, '1')}")

    # Wait for the hub to answer, and for the tunnel URL to be published.
    deadline = time.time() + 45
    reachable = False
    while time.time() < deadline:
        latest = HubConfig.load(cfg.session_id, cfg.home)
        if latest is not None:
            cfg = latest
        try:
            with HubClient(cfg.local_url, timeout=2.0) as probe:
                probe.health()
            reachable = True
            break
        except HubError:
            time.sleep(0.4)

    if not reachable:
        fail("the hub did not come up — see " + str(log))
        return 1

    if cfg.public_url:
        ok(f"ngrok tunnel up  {dim(ngrok_version() or '')}")
    elif args.no_tunnel:
        warn("tunnel disabled (--no-tunnel)")
    else:
        warn("no public tunnel")
        print(NO_NGROK_HELP.format(port=cfg.port))

    # The host is participant #0: it joins its own session and comes up
    # listening, so it is live before anyone else connects.
    profile = SessionProfile(
        session_id=cfg.session_id, url=cfg.public_url or cfg.local_url,
        name=cfg.host_name, host_name=cfg.host_name, token=cfg.host_token,
        is_host=True, room=DEFAULT_ROOM, home=cfg.home,
    )
    profile.save()
    if orphans := stop_orphans(cfg.home, keep=cfg.session_id):
        ok(f"stopped {len(orphans)} leftover session listener(s)")
    if args.focus:
        try:
            with HubClient(profile.url, profile.token) as client:
                client.send(Envelope(kind=KIND_HELLO, sender=name, room=DEFAULT_ROOM,
                                     text=args.focus, body=ctx_gather(args.focus)))
        except HubError as exc:
            warn(f"could not announce your focus: {exc}")
    status = onboard.ensure_daemon(profile) if not args.no_daemon else {}
    if status.get("state") == "live":
        ok("listening")
    elif not args.no_daemon:
        warn("the daemon did not report live yet; check `collab status`")

    heading("Share this one line with the other person")
    print("  " + c(join_line(cfg), "1;32"))
    if not cfg.public_url:
        print(dim(f"  (local only — LAN address is http://{local_ip()}:{cfg.port})"))

    _monitor_hint(profile, status)
    print()
    return 0


def cmd_join(args: argparse.Namespace) -> int:
    _warn_outside_venv()
    ensure_home()
    try:
        profile, snapshot, status = onboard.join_session(
            args.url, name=args.name, focus=args.focus,
            start_daemon=not args.no_daemon,
        )
    except (ValueError, HubError) as exc:
        fail(str(exc))
        return 1

    if orphans := stop_orphans(profile.home, keep=profile.session_id):
        ok(f"stopped {len(orphans)} leftover session listener(s)")
    ok(f"joined {c(profile.session_id, '36')} as {c(profile.name, '1')}"
       f" (host: {profile.host_name})")
    if status.get("state") == "live":
        ok("listening")
    elif not args.no_daemon:
        warn("the daemon did not report live yet; check `collab status`")
    if args.focus:
        ok(f"announced your focus: {args.focus}")

    # The snapshot in the join response predates our own feed connecting, so it
    # would show us as offline. Re-read it now that we are live.
    if status.get("state") == "live":
        try:
            with _client(profile) as client:
                fresh = client.snapshot()
            snapshot = {**snapshot, **fresh}
        except HubError:
            pass

    _print_snapshot(snapshot, profile.name)
    _monitor_hint(profile, status)
    print()
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    profile = _require_profile(args)
    text = " ".join(args.text).strip()
    if not text:
        fail("nothing to send")
        return 1
    env = Envelope(
        kind=KIND_CHAT, text=text, sender=profile.name,
        room=None if args.to else (args.room or profile.room),
        to=args.to, thread=args.thread,
    )
    try:
        with _client(profile) as client:
            client.send(env)
    except HubError as exc:
        fail(str(exc))
        return 1
    target = f"@{args.to}" if args.to else f"#{args.room or profile.room}"
    ok(f"sent to {target}")
    return 0


def cmd_listen(args: argparse.Namespace) -> int:
    """Stream events as lines.  This is what a Monitor watches."""
    profile = _require_profile(args)
    inbox = Inbox(profile.dir)
    path = inbox.jsonl
    path.touch(exist_ok=True)

    if not args.follow:
        for env in inbox.all_events(limit=args.limit):
            print(_format(env, args.json))
        return 0

    if args.replay:
        for env in inbox.all_events(limit=args.replay):
            print(_format(env, args.json), flush=True)

    with path.open("r", encoding="utf-8") as fh:
        fh.seek(0, os.SEEK_END)
        while True:
            line = fh.readline()
            if not line:
                if args.exit_when_idle and is_running(profile) is None:
                    return 0
                time.sleep(0.25)
                continue
            try:
                env = Envelope.from_dict(json.loads(line))
            except ValueError:
                continue
            if args.room and env.room != args.room:
                continue
            if args.mine_too is False and env.sender == profile.name:
                continue
            # flush on every line: a Monitor only sees what is actually written.
            print(_format(env, args.json), flush=True)


def _format(env: Envelope, as_json: bool) -> str:
    return json.dumps(env.to_dict(), ensure_ascii=False) if as_json else env.render_line()


def cmd_recv(args: argparse.Namespace) -> int:
    """Drain unread messages; optionally wait for one to arrive."""
    profile = _require_profile(args)
    inbox = Inbox(profile.dir)
    deadline = time.time() + args.wait
    while True:
        events = inbox.take_unread(limit=args.limit, mark=not args.peek)
        events = [e for e in events if args.mine_too or e.sender != profile.name]
        if events:
            for env in events:
                print(_format(env, args.json))
            return 0
        if time.time() >= deadline:
            return 0
        time.sleep(0.3)


def cmd_who(args: argparse.Namespace) -> int:
    profile = _require_profile(args)
    try:
        with _client(profile) as client:
            snapshot = client.participants()
    except HubError as exc:
        fail(str(exc))
        return 1
    if args.json:
        print(json.dumps(snapshot, indent=2))
        return 0
    _print_snapshot(snapshot, profile.name)
    print()
    return 0


def cmd_rooms(args: argparse.Namespace) -> int:
    profile = _require_profile(args)
    try:
        with _client(profile) as client:
            rooms = client.create_room(args.create) if args.create else client.rooms()
    except HubError as exc:
        fail(str(exc))
        return 1
    for r in rooms:
        marker = "*" if r == profile.room else " "
        print(f" {marker} #{r}")
    return 0


def cmd_task(args: argparse.Namespace) -> int:
    profile = _require_profile(args)
    try:
        with _client(profile) as client:
            if args.action == "list":
                tasks = client.tasks(open_only=args.open)
                if args.json:
                    print(json.dumps(tasks, indent=2))
                    return 0
                if not tasks:
                    print(dim("  no tasks yet — propose one with `collab task propose \"...\"`"))
                for t in tasks:
                    owner = t.get("owner") or dim("unclaimed")
                    print(f"  {t['id']}  {t['title']}  [{_short_state(t['state'])}]  {owner}")
                return 0
            task = client.task_action(
                args.action, task_id=args.id, title=args.title or "",
                detail=args.detail or "", room=args.room,
            )
    except HubError as exc:
        fail(str(exc))
        return 1
    ok(f"{args.action}: {task['id']}  {task['title']}  "
       f"[{_short_state(task['state'])}]  {task.get('owner') or 'unclaimed'}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """A readable live transcript, for a person to leave open in a pane."""
    from .client import watch as w

    profile = _require_profile(args)

    if args.tmux:
        argv = [str(Path(sys.argv[0]).resolve()), "watch",
                "--session", profile.session_id]
        passthrough = {k: os.environ[k] for k in ("COLLAB_HOME", "COLLAB_CONFIG",
                                                  "COLLAB_NAME", "NO_COLOR")
                       if k in os.environ}
        try:
            where = w.open_tmux_pane(argv, env=passthrough, percent=args.percent,
                                     horizontal=not args.vertical)
        except RuntimeError as exc:
            fail(str(exc))
            if not w.tmux_available():
                print(dim("  install tmux, or just run `collab watch` in a second terminal"))
            elif not w.in_tmux():
                print(dim("  start tmux first, then re-run this inside it:"))
                joined = " ".join(argv)
                print(dim(f"    tmux new-session -s collab '{joined}'"))
            return 1
        ok(where)
        print(dim("  the conversation will appear there as it happens"))
        return 0

    try:
        return w.watch(profile, follow=not args.no_follow, limit=args.limit)
    except KeyboardInterrupt:
        print()
        return 0


def cmd_file(args: argparse.Namespace) -> int:
    profile = _require_profile(args)
    try:
        with _client(profile) as client:
            if args.action == "send":
                path = Path(args.target).expanduser()
                if not path.is_file():
                    fail(f"no such file: {path}")
                    return 1
                size = path.stat().st_size
                if size > MAX_FILE_BYTES:
                    fail(f"{path.name} is {size / 1024 / 1024:.1f}MB — the limit is "
                         f"{MAX_FILE_BYTES // 1024 // 1024}MB")
                    return 1
                record = client.upload_file(path, to=args.to, room=args.room)
                target = f"@{args.to}" if args.to else f"#{args.room or profile.room}"
                ok(f"shared {record['name']} ({size / 1024:.0f} KB) with {target}")
                print(f"       {dim('they fetch it with: collab file get ' + record['id'])}")
                print(f"       {dim('it is deleted from the host once they confirm receipt')}")
                return 0

            if args.action == "list":
                files = client.list_files()
                if args.json:
                    print(json.dumps(files, indent=2))
                    return 0
                if not files:
                    print(dim("  no files waiting"))
                    return 0
                for f in files:
                    who = f"→ {f['recipient']}" if f["recipient"] else f"#{f['room']}"
                    print(f"  {f['id']}  {f['name']}  "
                          f"{f['size'] / 1024:.0f} KB  from {f['sender']} {who}")
                return 0

            if args.action == "get":
                dest_dir = Path(args.output or ".").expanduser()
                record = next((f for f in client.list_files() if f["id"] == args.target), None)
                path, digest = client.download_file(args.target, dest_dir)
                if record and record["sha256"] != digest:
                    # Never confirm receipt of something that arrived corrupt —
                    # acking is what deletes the only copy.
                    path.unlink(missing_ok=True)
                    fail("checksum mismatch — the download was corrupt, not confirming receipt")
                    return 1
                ok(f"saved {path} ({path.stat().st_size / 1024:.0f} KB, checksum verified)")
                if args.keep:
                    warn("--keep: leaving the file on the host (it expires in 24h)")
                    return 0
                client.ack_file(args.target)
                ok("confirmed receipt — the host has deleted its copy")
                return 0

            if args.action == "rm":
                client.delete_file(args.target)
                ok(f"withdrew {args.target}")
                return 0
    except HubError as exc:
        fail(str(exc))
        return 1
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    profile = SessionProfile.current()
    if profile is None:
        if args.json:
            print(json.dumps({"active": False, "home": str(collab_home())}))
        else:
            print(f"no active session in {collab_home()}")
        return 0
    status = read_status(profile)
    pid = is_running(profile)
    payload = {
        "active": True,
        "home": profile.home,
        "session_id": profile.session_id,
        "name": profile.name,
        "host": profile.host_name,
        "is_host": profile.is_host,
        "url": profile.url,
        "daemon_pid": pid,
        "monitor_command": f"{sys.argv[0]} listen --follow",
        "monitor_ws": (f"ws://127.0.0.1:{status['bridge_port']}/events"
                       if status.get("bridge_port") else None),
        **{k: status.get(k) for k in
           ("state", "others_connected", "others_total", "unread", "last_seq")},
    }
    if hint := status.get("hint"):
        payload["hint"] = hint
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0
    heading(f"collab session {payload['session_id']}")
    for key in ("name", "host", "url", "state", "others_connected", "unread",
                "last_seq", "daemon_pid", "monitor_command", "monitor_ws"):
        if payload.get(key) is not None:
            print(f"  {key:<16} {payload[key]}")
    if payload.get("hint"):
        print(f"\n  {c(payload['hint'], '33')}")
    print(f"  {'state dir':<16} {profile.dir}")
    print()
    return 0


def cmd_url(args: argparse.Namespace) -> int:
    profile = _require_profile(args)
    cfg = HubConfig.load(profile.session_id, profile.home)
    if cfg is None:
        fail("only the host can print the invite line for a session")
        return 1
    print(join_line(cfg))
    if cfg.tunnel == "ngrok" and not cfg.domain:
        print(dim("  (a free tunnel gets a new address if it restarts — re-run this to "
                  "get the current link, or use `collab host --domain` to pin one)"))
    return 0


def cmd_kick(args: argparse.Namespace) -> int:
    profile = _require_profile(args)
    try:
        with _client(profile) as client:
            removed = client.revoke(args.name)
    except HubError as exc:
        fail(str(exc))
        return 1
    ok(f"removed {removed} — their token no longer works")
    return 0


def cmd_name(args: argparse.Namespace) -> int:
    if not args.value:
        print(resolve_name())
        return 0
    final = set_default_name(args.value)
    ok(f"default display name is now {c(final, '1')}")
    profile = SessionProfile.current()
    if profile is not None:
        try:
            with _client(profile) as client:
                new = client.rename(final)
            profile.name = new
            profile.save()
            ok(f"renamed in the active session to {new}")
        except HubError as exc:
            warn(f"could not rename in the active session: {exc}")
    return 0


def cmd_daemon(args: argparse.Namespace) -> int:
    profile = _require_profile(args)
    if args.action == "status":
        pid = is_running(profile)
        print(json.dumps({"pid": pid, **read_status(profile)}, indent=2))
        return 0
    if args.action == "stop":
        print("stopped" if stop_daemon(profile) else "was not running")
        return 0
    status = onboard.ensure_daemon(profile)
    ok(f"daemon {status.get('state', 'starting')}")
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    from . import skills as sk

    if args.action == "status":
        print(json.dumps(sk.status(), indent=2))
        return 0
    try:
        result = (sk.uninstall() if args.action == "uninstall"
                  else sk.install(copy=args.copy, force=args.force))
    except RuntimeError as exc:
        fail(str(exc))
        return 1

    verb = "removed" if args.action == "uninstall" else (
        "linked" if result.linked else "installed")
    if result.installed:
        ok(f"{verb} {len(result.installed)} skills into {result.target}")
        for name in result.installed:
            print(f"       {dim(name)}")
    else:
        warn(f"nothing to {args.action} in {result.target}")
    for name in result.skipped:
        warn(f"{name} already exists and was not written — pass --force to replace it")
    if args.action == "install" and result.installed:
        print(dim("       restart your agent so it picks the skills up"))
    return 0


def cmd_statusline(args: argparse.Namespace) -> int:
    from .statusline import install as sli
    from .statusline import render as slr

    if args.action == "render":
        extra: list[str] = []
        if args.plain:
            extra.append("--plain")
        if args.json:
            extra.append("--json")
        if args.cwd:
            extra += ["--cwd", args.cwd]
        if args.width:
            extra += ["--width", str(args.width)]
        return slr.main(extra)
    if args.action == "status":
        print(json.dumps(sli.status(args.agent, args.scope), indent=2))
        return 0
    try:
        result = (sli.uninstall(args.agent, args.scope) if args.action == "uninstall"
                  else sli.install(args.agent, args.scope))
    except RuntimeError as exc:
        fail(str(exc))
        return 1
    if result.action == "instructions":
        print("\n".join(result.notes))
        return 0
    ok(f"{result.action}: {result.script}")
    for note in result.notes:
        print(f"       {dim(note)}")
    for b in result.backups:
        print(f"       {dim('backup: ' + str(b))}")
    if result.action not in ("absent",):
        print(dim("       restart Claude Code, or it will keep the old status line"))
    return 0


# --- parser --------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="collab",
        description="An A2A hub that lets coding agents talk, align on tasks, and discuss work.",
    )
    p.add_argument("--version", action="version", version=f"collab {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_session_flag(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--session", help="act on this session id instead of the current one")

    h = sub.add_parser("host", help="start a session and print a link to share")
    h.add_argument("--name", help="your display name (default: your global collab name)")
    h.add_argument("--port", type=int, help="port to bind (default: a free one)")
    h.add_argument("--bind", default="127.0.0.1",
                   help="interface to bind; 0.0.0.0 exposes it on your LAN")
    h.add_argument("--focus", default="", help="what you are working on, shown to others")
    h.add_argument("--domain", default="",
                   help="a reserved ngrok domain, so the URL survives a tunnel restart")
    h.add_argument("--no-tunnel", action="store_true", help="skip ngrok even if installed")
    h.add_argument("--no-daemon", action="store_true", help="do not start listening")
    h.set_defaults(func=cmd_host)

    j = sub.add_parser("join", help="join a session and start collaborating")
    j.add_argument("url", help="the join URL, e.g. https://host#INVITE")
    j.add_argument("--name", help="your display name")
    j.add_argument("--focus", default="", help="what you are working on, announced on arrival")
    j.add_argument("--no-daemon", action="store_true", help="do not start listening")
    j.set_defaults(func=cmd_join)

    s = sub.add_parser("send", help="send a message")
    s.add_argument("text", nargs="+")
    s.add_argument("--room", help="room to post in (default: your current room)")
    s.add_argument("--to", help="send privately to one participant")
    s.add_argument("--thread", help="thread id to reply in")
    add_session_flag(s)
    s.set_defaults(func=cmd_send)

    l = sub.add_parser("listen", help="stream events as lines (arm a Monitor on this)")
    l.add_argument("--follow", "-f", action="store_true", help="keep streaming as events arrive")
    l.add_argument("--json", action="store_true", help="emit raw JSON instead of formatted lines")
    l.add_argument("--room", help="only this room")
    l.add_argument("--limit", type=int, default=50, help="how many past events to print")
    l.add_argument("--replay", type=int, default=0, help="replay this many past events first")
    l.add_argument("--mine-too", action="store_true", default=False,
                   help="include your own messages")
    l.add_argument("--exit-when-idle", action="store_true",
                   help="stop if the daemon is not running")
    add_session_flag(l)
    l.set_defaults(func=cmd_listen)

    r = sub.add_parser("recv", help="drain unread messages, optionally waiting")
    r.add_argument("--wait", type=float, default=0.0, help="seconds to wait for a message")
    r.add_argument("--limit", type=int, default=100)
    r.add_argument("--json", action="store_true")
    r.add_argument("--peek", action="store_true", help="do not mark as read")
    r.add_argument("--mine-too", action="store_true", default=False)
    add_session_flag(r)
    r.set_defaults(func=cmd_recv)

    w = sub.add_parser("who", help="who is in the session and what they are doing")
    w.add_argument("--json", action="store_true")
    add_session_flag(w)
    w.set_defaults(func=cmd_who)

    rm = sub.add_parser("rooms", help="list or create rooms")
    rm.add_argument("--create", help="create a room with this name")
    add_session_flag(rm)
    rm.set_defaults(func=cmd_rooms)

    t = sub.add_parser("task", help="the shared task board")
    t.add_argument("action", choices=["propose", "claim", "update", "complete",
                                      "fail", "cancel", "list"])
    t.add_argument("title", nargs="?", help="title when proposing")
    t.add_argument("--id", help="task id for claim/update/complete")
    t.add_argument("--detail", help="longer description")
    t.add_argument("--room")
    t.add_argument("--open", action="store_true", help="list only open tasks")
    t.add_argument("--json", action="store_true")
    add_session_flag(t)
    t.set_defaults(func=cmd_task)

    wa = sub.add_parser("watch", help="a readable live transcript of the conversation")
    wa.add_argument("--tmux", action="store_true",
                    help="open it in a new tmux pane instead of here")
    wa.add_argument("--vertical", action="store_true",
                    help="with --tmux, split below instead of to the right")
    wa.add_argument("--percent", type=int, default=35,
                    help="with --tmux, how much of the window to give the pane")
    wa.add_argument("--no-follow", action="store_true", help="print and exit")
    wa.add_argument("--limit", type=int, default=200, help="how much history to show")
    add_session_flag(wa)
    wa.set_defaults(func=cmd_watch)

    f = sub.add_parser("file", help="share files and artifacts without pasting them as text")
    f.add_argument("action", choices=["send", "get", "list", "rm"])
    f.add_argument("target", nargs="?", help="path to send, or file id to get/remove")
    f.add_argument("--to", help="share privately with one participant")
    f.add_argument("--room")
    f.add_argument("--output", "-o", help="directory to save into (default: here)")
    f.add_argument("--keep", action="store_true",
                   help="do not confirm receipt, so the host keeps its copy")
    f.add_argument("--json", action="store_true")
    add_session_flag(f)
    f.set_defaults(func=cmd_file)

    st = sub.add_parser("status", help="connection status for this repo")
    st.add_argument("--json", action="store_true")
    st.set_defaults(func=cmd_status)

    u = sub.add_parser("url", help="reprint the join line (host only)")
    add_session_flag(u)
    u.set_defaults(func=cmd_url)

    k = sub.add_parser("kick", help="remove a participant (host only)")
    k.add_argument("name")
    add_session_flag(k)
    k.set_defaults(func=cmd_kick)

    n = sub.add_parser("name", help="show or set your global display name")
    n.add_argument("value", nargs="?")
    n.set_defaults(func=cmd_name)

    d = sub.add_parser("daemon", help="manage the listener")
    d.add_argument("action", choices=["start", "stop", "status"], nargs="?", default="status")
    add_session_flag(d)
    d.set_defaults(func=cmd_daemon)

    sk = sub.add_parser("skills", help="install collab's skills into your coding agent")
    sk.add_argument("action", choices=["install", "uninstall", "status"])
    sk.add_argument("--copy", action="store_true",
                    help="copy the skills instead of symlinking them")
    sk.add_argument("--force", action="store_true",
                    help="replace skills of the same name that are already there")
    sk.set_defaults(func=cmd_skills)

    sl = sub.add_parser("statusline", help="the Claude Code status line segment")
    sl.add_argument("action", choices=["install", "uninstall", "status", "render"])
    sl.add_argument("--agent", default="auto",
                    choices=["auto", "claude-code", "tmux", "generic"],
                    help="which host to wire up (default: detect)")
    sl.add_argument("--scope", choices=["global", "project"], default="global")
    sl.add_argument("--plain", action="store_true", help="render without ANSI colour")
    sl.add_argument("--json", action="store_true", help="render structured output")
    sl.add_argument("--cwd", help="render the session for this directory")
    sl.add_argument("--width", type=int, help="truncate the rendered line")
    sl.set_defaults(func=cmd_statusline)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
