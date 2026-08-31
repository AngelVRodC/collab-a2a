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
import shutil
import subprocess
import sys
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


def collab_executable() -> str:
    """Absolute path to this collab.

    Both installers write our path into someone else's config file, and those
    run in a bare shell where PATH may not have us on it.
    """
    exe = Path(sys.argv[0])
    if exe.name.startswith("collab") and exe.exists():
        return str(exe.resolve())
    guess = Path(sys.executable).with_name("collab")
    if guess.exists():
        return str(guess.resolve())
    return shutil.which("collab") or "collab"


def short_executable() -> str:
    """How to write our command for a human or an agent to read.

    The absolute path is right for a status line, which runs in a bare shell.
    It is wrong for instructions someone will run in their own terminal: thirteen
    repetitions of a 40-character path is noise, and for an agent it is context
    spent on nothing. Use the bare name whenever PATH already resolves to us.
    """
    full = collab_executable()
    on_path = shutil.which("collab")
    if on_path:
        try:
            if Path(on_path).resolve() == Path(full).resolve():
                return "collab"
        except OSError:
            pass
    return full


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


#: Sharing usage is on by default: the whole point is that an agent can weigh
#: up who has quota left before handing out work.
SHARE_STATS_DEFAULT = True


def share_stats_enabled() -> bool:
    value = load_config().get("share_stats")
    return SHARE_STATS_DEFAULT if value is None else bool(value)


def set_share_stats(enabled: bool) -> bool:
    cfg = load_config()
    cfg["share_stats"] = bool(enabled)
    save_config(cfg)
    return bool(enabled)


#: How `collab watch` arranges itself.
#:
#: ``split``  one window, roster above the conversation (works anywhere)
#: ``tmux``   two real tmux panes, so tmux resizes and moves them for you
#: ``chat``   conversation only
#: ``roster`` roster only
WATCH_LAYOUTS = ("split", "tmux", "chat", "roster")
DEFAULT_WATCH_LAYOUT = "split"
DEFAULT_ROSTER_SIZE = 30
DEFAULT_ROSTER_POSITION = "top"


def watch_settings() -> dict[str, Any]:
    """The saved viewer preferences, with sane defaults filled in."""
    cfg = load_config()
    layout = str(cfg.get("watch_layout") or DEFAULT_WATCH_LAYOUT)
    if layout not in WATCH_LAYOUTS:
        layout = DEFAULT_WATCH_LAYOUT
    try:
        size = int(cfg.get("watch_roster_size") or DEFAULT_ROSTER_SIZE)
    except (TypeError, ValueError):
        size = DEFAULT_ROSTER_SIZE
    position = str(cfg.get("watch_roster_position") or DEFAULT_ROSTER_POSITION)
    if position not in ("top", "bottom", "left", "right"):
        position = DEFAULT_ROSTER_POSITION
    return {"layout": layout, "roster_size": max(5, min(size, 90)),
            "roster_position": position}


def save_watch_settings(*, layout: str | None = None, roster_size: int | None = None,
                        roster_position: str | None = None) -> dict[str, Any]:
    cfg = load_config()
    if layout:
        cfg["watch_layout"] = layout
    if roster_size:
        cfg["watch_roster_size"] = int(roster_size)
    if roster_position:
        cfg["watch_roster_position"] = roster_position
    save_config(cfg)
    return watch_settings()


#: How often to re-run the usage command, in seconds. Usage moves slowly; this
#: is about keeping the roster honest, not about precision.
DEFAULT_STATS_INTERVAL = 120


def stats_source() -> tuple[str, int]:
    """A command that prints this agent's usage as JSON, and how often to run it.

    Agents whose host tool has no status line cannot be pushed figures, and
    relying on the agent to remember to report is relying on diligence. A
    command the daemon runs on a timer needs no diligence at all.
    """
    cfg = load_config()
    command = str(cfg.get("stats_command") or "")
    try:
        interval = int(cfg.get("stats_interval") or DEFAULT_STATS_INTERVAL)
    except (TypeError, ValueError):
        interval = DEFAULT_STATS_INTERVAL
    return command, max(15, interval)


def set_stats_source(command: str | None = None,
                     interval: int | None = None) -> tuple[str, int]:
    cfg = load_config()
    if command is not None:
        if command:
            cfg["stats_command"] = command
        else:
            cfg.pop("stats_command", None)
    if interval:
        cfg["stats_interval"] = int(interval)
    save_config(cfg)
    return stats_source()


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
    #: Stable identity on the hub. ``name`` is a label that can change; this
    #: does not, so it is what the daemon uses to recognise itself.
    participant_id: str = ""

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
    def load_from(cls, directory: Path) -> SessionProfile | None:
        """Load a profile by its directory, without consulting the pointer."""
        p = Path(directory) / "profile.json"
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
