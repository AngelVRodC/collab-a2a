"""Creating and locating a hosted session on this machine."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from ..config import collab_home, ensure_home
from .auth import new_secret
from .store import Store


@dataclass
class HubConfig:
    """What the detached hub process needs in order to come up."""

    session_id: str
    host_name: str
    port: int
    bind: str
    invite: str
    host_token: str
    public_url: str = ""
    tunnel: str = "none"
    pid: int = 0
    home: str = ""

    def __post_init__(self) -> None:
        if not self.home:
            self.home = str(collab_home())

    @property
    def dir(self) -> Path:
        # Resolved from the recorded home, never from the process cwd — the hub
        # runs detached and may not be started from the repo.
        return Path(self.home) / "sessions" / self.session_id

    @property
    def db_path(self) -> Path:
        return self.dir / "hub.db"

    @property
    def local_url(self) -> str:
        host = "127.0.0.1" if self.bind in ("127.0.0.1", "localhost") else self.bind
        return f"http://{host}:{self.port}"

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / "hub.json"
        p.write_text(json.dumps(asdict(self), indent=2) + "\n")
        os.chmod(p, 0o600)  # holds the invite and the host token

    @classmethod
    def load(cls, session_id: str, home: Path | str | None = None) -> HubConfig | None:
        base = Path(home) if home else collab_home()
        p = base / "sessions" / session_id / "hub.json"
        if not p.exists():
            return None
        try:
            return cls(**json.loads(p.read_text()))
        except (OSError, ValueError, TypeError):
            return None


def new_session_id() -> str:
    return "s_" + secrets.token_hex(4)


def create_session(host_name: str, port: int, bind: str = "127.0.0.1") -> HubConfig:
    """Mint a session with fresh credentials and seed its store."""
    ensure_home()
    cfg = HubConfig(
        session_id=new_session_id(),
        host_name=host_name,
        port=port,
        bind=bind,
        invite=new_secret(),
        host_token=new_secret(),
    )
    cfg.save()

    store = Store(cfg.db_path)
    # An unlimited-use invite, valid for a day; the host can always mint another.
    store.add_invite(cfg.invite, ttl_seconds=24 * 3600, max_uses=0)
    store.add_participant(cfg.host_name, cfg.host_token, is_host=True)
    store.add_room("general", cfg.host_name)
    store.close()
    return cfg


def join_line(cfg: HubConfig) -> str:
    """The single line a host hands to someone else."""
    base = cfg.public_url or cfg.local_url
    return f"collab join {base}#{cfg.invite}"
