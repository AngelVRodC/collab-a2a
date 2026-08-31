"""Who is using this repo's collab state, written down where anyone can look.

Occupancy used to be inferred: scan `.collab/sessions/*/`, load each profile,
test whether its listener pid is alive. That works, but it is invisible — an
agent (or a person) looking at a repo cannot see that another agent is in a
session here, and nothing says who, since when, or in which state directory.

So the fact is recorded rather than deduced: one small file at the root of
`.collab/`, written when an agent enters a session and removed when it leaves.

A lock file that outlives its process is the classic failure of this pattern,
so nothing here trusts the file alone. It carries the pids that back it — the
hub and the listener — and it counts as *held* only while one of them is
alive. A lock whose processes are gone is stale by definition, and stale locks
are cleared automatically rather than needing a human. The one case that is not
decidable from here — every pid alive, but the session itself unreachable — is
the case that asks.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LOCK_NAME = "agent.lock"


def lock_path(home: Path | str | None = None) -> Path:
    if home is None:
        # Imported here: config asks *us* which directories are claimed, so a
        # module-level import in this direction would be a cycle.
        from .config import collab_home

        home = collab_home()
    return Path(home) / LOCK_NAME


def _alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


@dataclass
class Lock:
    """The claim one agent has on this repo's collab state."""

    name: str
    session_id: str
    role: str = "guest"          # "host" or "guest"
    url: str = ""
    state_dir: str = ""          # set when this agent has its own
    hub_pid: int = 0
    listener_pid: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def pids(self) -> list[int]:
        return [p for p in (self.hub_pid, self.listener_pid) if p]

    @property
    def held(self) -> bool:
        """A claim is only as real as the processes behind it.

        Either pid is enough: a host whose listener has stopped still has a hub
        serving the session, and a guest has no hub at all.
        """
        return any(_alive(pid) for pid in self.pids)

    @property
    def stale(self) -> bool:
        return not self.held

    def age(self) -> float:
        return max(time.time() - self.created_at, 0.0)

    def describe(self) -> str:
        where = f" in {Path(self.state_dir).name}" if self.state_dir else ""
        return f"{self.name} ({self.role}) in {self.session_id}{where}"


def read(home: Path | str | None = None) -> Lock | None:
    path = lock_path(home)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    known = {f for f in Lock.__dataclass_fields__}
    try:
        return Lock(**{k: v for k, v in data.items() if k in known})
    except TypeError:
        return None


def holder(home: Path | str | None = None) -> Lock | None:
    """The lock only if it is genuinely held; stale ones are cleared."""
    lock = read(home)
    if lock is None:
        return None
    if lock.held:
        return lock
    release(home)
    return None


def acquire(lock: Lock, home: Path | str | None = None) -> Path:
    """Claim this repo, or refresh a claim we already hold."""
    path = lock_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock.updated_at = time.time()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(lock), indent=2))
    tmp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def refresh(home: Path | str | None = None, **fields: Any) -> Lock | None:
    """Update the pids or the state directory on a lock we already hold."""
    lock = read(home)
    if lock is None:
        return None
    for key, value in fields.items():
        if hasattr(lock, key):
            setattr(lock, key, value)
    acquire(lock, home)
    return lock


def release(home: Path | str | None = None) -> bool:
    """Give up the claim. Missing is success — the point is that it is gone."""
    try:
        lock_path(home).unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def is_ours(lock: Lock | None, session_id: str) -> bool:
    """Our own session's lock is not somebody else being here."""
    return lock is not None and lock.session_id == session_id
