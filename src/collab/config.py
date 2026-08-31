"""Where state lives, and how a participant's name is resolved.

Session state is **per repo**: a ``.collab/`` directory at the repository root
(or the current directory when that is not a repo).  Two checkouts on one
machine therefore hold two independent sessions, which is exactly what you want
when two agents on the same box are working on different projects.

Only the default display name is global — that is a property of the person, not
of the project.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

COLLAB_DIRNAME = ".collab"

#: Everything in .collab is either a secret (bearer tokens, invites) or local
#: scratch state, so it must never be committed.
GITIGNORE_BODY = """\
# Created by collab. Holds session tokens and local state — never commit this.
*
"""


def repo_root(start: Path | None = None) -> Path:
    """The git top level, or the given directory when it is not a repo."""
    start = Path(start or Path.cwd()).resolve()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=3, cwd=str(start), check=False,
        )
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return start


def collab_home(cwd: Path | None = None) -> Path:
    """The per-repo state directory.

    ``COLLAB_HOME`` overrides it outright, which is what lets a second profile
    (and the tests) run against the same repo without colliding.
    """
    if override := os.environ.get("COLLAB_HOME"):
        return Path(override)
    return repo_root(cwd) / COLLAB_DIRNAME


def ensure_home(cwd: Path | None = None) -> Path:
    """Create ``.collab/`` on first use, with its own .gitignore."""
    home = collab_home(cwd)
    home.mkdir(parents=True, exist_ok=True)
    gitignore = home / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE_BODY)
    return home


# --- global (per-person) settings -------------------------------------------

def global_config_path() -> Path:
    if override := os.environ.get("COLLAB_CONFIG"):
        return Path(override)
    return Path.home() / ".config" / "collab" / "config.json"


def load_config() -> dict[str, Any]:
    p = global_config_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict[str, Any]) -> None:
    p = global_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n")


def _git_user_name() -> str | None:
    try:
        out = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True, text=True, timeout=2, check=False,
        ).stdout.strip()
        return out or None
    except (OSError, subprocess.SubprocessError):
        return None


def _slug(name: str) -> str:
    cleaned = "".join(c if (c.isalnum() or c in "-_") else "-" for c in name.strip())
    return cleaned.strip("-").lower() or "agent"


def resolve_name(explicit: str | None = None) -> str:
    """--name  >  $COLLAB_NAME  >  global config  >  git user.name  >  $USER."""
    for candidate in (
        explicit,
        os.environ.get("COLLAB_NAME"),
        load_config().get("display_name"),
        _git_user_name(),
        os.environ.get("USER") or os.environ.get("USERNAME"),
    ):
        if candidate and str(candidate).strip():
            return _slug(str(candidate))
    return "agent"


def set_default_name(name: str) -> str:
    cfg = load_config()
    cfg["display_name"] = _slug(name)
    save_config(cfg)
    return cfg["display_name"]


# --- per-repo session state ---------------------------------------------------

def sessions_dir(cwd: Path | None = None) -> Path:
    return collab_home(cwd) / "sessions"


def session_dir(session_id: str, cwd: Path | None = None) -> Path:
    return sessions_dir(cwd) / session_id


def current_pointer(cwd: Path | None = None) -> Path:
    """Names the session this repo is currently working in."""
    return collab_home(cwd) / "current"


@dataclass
class SessionProfile:
    """Everything needed to rejoin without asking again."""

    session_id: str
    url: str
    name: str
    host_name: str
    token: str
    is_host: bool = False
    room: str = "general"
    bridge_port: int | None = None
    home: str = ""

    def __post_init__(self) -> None:
        if not self.home:
            self.home = str(collab_home())

    @property
    def dir(self) -> Path:
        return Path(self.home) / "sessions" / self.session_id

    def save(self) -> None:
        ensure_home(Path(self.home).parent if self.home else None)
        Path(self.home).mkdir(parents=True, exist_ok=True)
        gitignore = Path(self.home) / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(GITIGNORE_BODY)
        d = self.dir
        d.mkdir(parents=True, exist_ok=True)
        p = d / "profile.json"
        p.write_text(json.dumps(asdict(self), indent=2) + "\n")
        os.chmod(p, 0o600)  # contains the bearer token
        pointer = Path(self.home) / "current"
        pointer.write_text(self.session_id + "\n")

    @classmethod
    def load(cls, session_id: str, cwd: Path | None = None) -> SessionProfile | None:
        p = session_dir(session_id, cwd) / "profile.json"
        if not p.exists():
            return None
        try:
            return cls(**json.loads(p.read_text()))
        except (OSError, ValueError, TypeError):
            return None

    @classmethod
    def current(cls, cwd: Path | None = None) -> SessionProfile | None:
        pointer = current_pointer(cwd)
        if not pointer.exists():
            return None
        sid = pointer.read_text().strip()
        return cls.load(sid, cwd) if sid else None

    @classmethod
    def list_all(cls, cwd: Path | None = None) -> list[SessionProfile]:
        d = sessions_dir(cwd)
        if not d.exists():
            return []
        return [p for child in sorted(d.iterdir())
                if (p := cls.load(child.name, cwd)) is not None]
