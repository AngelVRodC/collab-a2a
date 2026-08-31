"""Fan-out core: one queue per connected participant.

``publish`` persists first and delivers second, so a message can never reach a
subscriber with a ``seq`` that is not already durable.  That ordering is what
lets a reconnecting client say "I have up to 412, continue from there" and get
a correct answer.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

from ..protocol import DEFAULT_ROOM, Envelope, KIND_PRESENCE
from .store import Store

QUEUE_MAXSIZE = 1000


@dataclass
class Subscription:
    participant: str
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(QUEUE_MAXSIZE))


class Hub:
    def __init__(self, store: Store, *, session_id: str, host_name: str) -> None:
        self.store = store
        self.session_id = session_id
        self.host_name = host_name
        self._subs: dict[str, list[Subscription]] = {}
        self._lock = asyncio.Lock()

    # --- subscriptions --------------------------------------------------------

    async def subscribe(self, participant: str) -> Subscription:
        sub = Subscription(participant=participant)
        async with self._lock:
            self._subs.setdefault(participant, []).append(sub)
        return sub

    async def unsubscribe(self, sub: Subscription) -> None:
        async with self._lock:
            subs = self._subs.get(sub.participant, [])
            if sub in subs:
                subs.remove(sub)
            if not subs:
                self._subs.pop(sub.participant, None)

    def connected(self) -> set[str]:
        return {name for name, subs in self._subs.items() if subs}

    def is_connected(self, name: str) -> bool:
        return bool(self._subs.get(name))

    # --- publishing -----------------------------------------------------------

    async def publish(self, env: Envelope) -> Envelope:
        """Persist, then push to every participant entitled to see it."""
        env = await asyncio.to_thread(self.store.append, env)
        await self._deliver(env)
        return env

    async def _deliver(self, env: Envelope) -> None:
        async with self._lock:
            targets = list(self._subs.items())
        for name, subs in targets:
            if not self._entitled(env, name):
                continue
            for sub in subs:
                try:
                    sub.queue.put_nowait(env)
                except asyncio.QueueFull:
                    # A consumer this far behind is not coming back; it will
                    # resume from its stored seq on reconnect rather than
                    # holding up delivery for everyone else.
                    with contextlib.suppress(asyncio.QueueEmpty):
                        sub.queue.get_nowait()
                    with contextlib.suppress(asyncio.QueueFull):
                        sub.queue.put_nowait(env)

    @staticmethod
    def _entitled(env: Envelope, name: str) -> bool:
        """A DM reaches only its two ends; anything else is room-wide.

        The sender gets their own message back too, which is what keeps every
        participant's local log identical and makes seq-based resume sound.
        """
        if env.to:
            return name in (env.to, env.sender)
        return True

    async def revoke(self, name: str) -> bool:
        ok = await asyncio.to_thread(self.store.revoke, name)
        if ok:
            async with self._lock:
                subs = self._subs.pop(name, [])
            for sub in subs:
                # None is the close sentinel the SSE generator watches for.
                with contextlib.suppress(asyncio.QueueFull):
                    sub.queue.put_nowait(None)
            await self.publish(Envelope(
                kind=KIND_PRESENCE, sender=name, room=DEFAULT_ROOM,
                body={"event": "removed from the session"},
            ))
        return ok

    # --- snapshot -------------------------------------------------------------

    def snapshot(self, viewer: str | None = None, *, history: int = 20) -> dict[str, Any]:
        """What a joining agent needs in order to say something useful at once."""
        connected = self.connected()
        people = []
        for p in self.store.participants():
            people.append({
                "name": p.name,
                "is_host": p.is_host,
                "connected": p.name in connected,
                "focus": p.meta.get("focus", ""),
                "repo": p.meta.get("repo", ""),
                "branch": p.meta.get("branch", ""),
            })
        return {
            "session_id": self.session_id,
            "host": self.host_name,
            "you": viewer,
            "rooms": self.store.rooms() or [DEFAULT_ROOM],
            "participants": people,
            "tasks": self.store.tasks(open_only=True),
            "recent": [e.to_dict() for e in self.store.history(viewer=viewer, limit=history)],
            "seq": self.store.max_seq(),
            "server_time": time.time(),
        }
