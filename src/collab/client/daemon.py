"""The daemon: the only thing that talks to the hub continuously.

It holds the SSE feed, survives drops by resuming from the last stored ``seq``,
and republishes every event locally three ways — JSONL for ``collab listen``,
SQLite for ``collab recv``, and a WebSocket frame for the bridge.  The agent
never has to know a reconnect happened.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from httpx_sse import aconnect_sse

from ..config import SessionProfile
from ..protocol import EXT_PREFIX, Envelope
from .bridge import Bridge
from .inbox import Inbox

logger = logging.getLogger(__name__)

BACKOFF_START = 0.5
BACKOFF_CAP = 30.0
#: The hub sends a keepalive every 15s; if we see nothing for well over that,
#: the connection is dead rather than quiet.
READ_TIMEOUT = 45.0
STATUS_HEARTBEAT = 3.0
#: A participant's `hello` is published while they join, which is *before*
#: their own feed subscribes — so a roster read triggered by that event still
#: shows them offline. Re-read it on a timer as well as on events.
SNAPSHOT_REFRESH = 9.0


@dataclass
class DaemonPaths:
    root: Path

    @property
    def pid(self) -> Path:
        return self.root / "daemon.pid"

    @property
    def status(self) -> Path:
        return self.root / "status.json"

    @property
    def log(self) -> Path:
        return self.root / "daemon.log"


def is_running(profile: SessionProfile) -> int | None:
    """Return the pid of a live daemon for this session, if there is one."""
    paths = DaemonPaths(profile.dir)
    if not paths.pid.exists():
        return None
    try:
        pid = int(paths.pid.read_text().strip())
    except (OSError, ValueError):
        return None
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        # Stale pid file from a crash; treat as not running.
        return None
    return pid


def stop(profile: SessionProfile) -> bool:
    pid = is_running(profile)
    if pid is None:
        return False
    with contextlib.suppress(OSError, ProcessLookupError):
        os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if is_running(profile) is None:
            return True
        time.sleep(0.1)
    with contextlib.suppress(OSError, ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    return True


def read_status(profile: SessionProfile) -> dict[str, Any]:
    p = DaemonPaths(profile.dir).status
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


class Daemon:
    def __init__(self, profile: SessionProfile, *, bridge_port: int = 0) -> None:
        self.profile = profile
        self.paths = DaemonPaths(profile.dir)
        self.inbox = Inbox(profile.dir)
        self.bridge = Bridge(port=bridge_port)
        self.state = "starting"
        self.last_event_at = time.time()
        self.connected_since: float | None = None
        self.snapshot: dict[str, Any] = {}
        self._http: httpx.AsyncClient | None = None
        self._stop = asyncio.Event()

    # --- status ---------------------------------------------------------------

    def write_status(self) -> None:
        """The status line reads only this file — never the network."""
        people = self.snapshot.get("participants", [])
        others = [p for p in people if p.get("name") != self.profile.name]
        payload = {
            "session_id": self.profile.session_id,
            "name": self.profile.name,
            "host": self.profile.host_name,
            "is_host": self.profile.is_host,
            "state": self.state,
            "url": self.profile.url,
            "bridge_port": self.bridge.port,
            "others_connected": sum(1 for p in others if p.get("connected")),
            "others_total": len(others),
            "unread": self.inbox.unread_count(),
            "last_seq": self.inbox.last_seq(),
            "heartbeat": time.time(),
            "connected_since": self.connected_since,
        }
        tmp = self.paths.status.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(self.paths.status)  # atomic: a reader never sees a half file

    async def _heartbeat_loop(self) -> None:
        last_refresh = 0.0
        while not self._stop.is_set():
            if (time.time() - last_refresh) > SNAPSHOT_REFRESH and self.state == "live":
                if self._http is not None:
                    await self._refresh_snapshot(self._http)
                last_refresh = time.time()
            self.write_status()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=STATUS_HEARTBEAT)

    async def _refresh_snapshot(self, client: httpx.AsyncClient) -> None:
        try:
            r = await client.get(
                f"{self.profile.url}{EXT_PREFIX}/participants",
                headers={"Authorization": f"Bearer {self.profile.token}"},
                timeout=10.0,
            )
            if r.status_code == 200:
                self.snapshot = r.json()
        except httpx.HTTPError:
            pass

    # --- the feed --------------------------------------------------------------

    async def run(self) -> None:
        self.paths.root.mkdir(parents=True, exist_ok=True)
        self.paths.pid.write_text(str(os.getpid()))
        await self.bridge.start()
        self.profile.bridge_port = self.bridge.port
        self.profile.save()
        self.write_status()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, self._stop.set)

        heartbeat = asyncio.create_task(self._heartbeat_loop())
        try:
            await self._connect_forever()
        finally:
            self._stop.set()
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat
            await self.bridge.stop()
            self.state = "stopped"
            self.write_status()
            with contextlib.suppress(OSError):
                self.paths.pid.unlink()

    async def _connect_forever(self) -> None:
        backoff = BACKOFF_START
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, read=READ_TIMEOUT)) as client:
            self._http = client
            while not self._stop.is_set():
                try:
                    await self._refresh_snapshot(client)
                    await self._stream_once(client)
                    backoff = BACKOFF_START
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # any drop is a reconnect, not a crash
                    self.state = "reconnecting"
                    self.connected_since = None
                    self.write_status()
                    logger.warning("feed dropped (%s); retrying in %.1fs", exc, backoff)
                    # Jitter keeps several agents from stampeding a restarted hub.
                    delay = backoff * (0.5 + random.random())
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=delay)
                    backoff = min(backoff * 2, BACKOFF_CAP)

    async def _stream_once(self, client: httpx.AsyncClient) -> None:
        resume = self.inbox.last_seq()
        # Always sent, including 0: on a first connect that backfills everything
        # said before we arrived, and on a reconnect it resumes exactly where we
        # left off. Either way the local log ends up gap-free.
        headers = {
            "Authorization": f"Bearer {self.profile.token}",
            "Last-Event-ID": str(resume),
        }

        url = f"{self.profile.url}{EXT_PREFIX}/events"
        async with aconnect_sse(client, "GET", url, headers=headers) as source:
            if source.response.status_code == 401:
                self.state = "unauthorized"
                self.write_status()
                raise RuntimeError("hub rejected our token (removed from the session?)")
            source.response.raise_for_status()
            self.state = "live"
            self.connected_since = time.time()
            self.write_status()

            async for event in source.aiter_sse():
                self.last_event_at = time.time()
                if self._stop.is_set():
                    break
                if event.event == "keepalive":
                    continue
                if event.event == "closed":
                    self.state = "unauthorized"
                    self.write_status()
                    raise RuntimeError("the hub closed our feed")
                if event.event == "ready":
                    await self._refresh_snapshot(client)
                    self.write_status()
                    continue
                if event.event != "collab":
                    continue
                try:
                    env = Envelope.from_dict(json.loads(event.data))
                except ValueError:
                    logger.warning("skipping unparseable event")
                    continue
                if self.inbox.record(env):
                    await self.bridge.broadcast(env)
                    if env.kind in ("hello", "presence"):
                        await self._refresh_snapshot(client)
                    self.write_status()


async def run_daemon(profile: SessionProfile, *, bridge_port: int = 0) -> None:
    await Daemon(profile, bridge_port=bridge_port).run()
