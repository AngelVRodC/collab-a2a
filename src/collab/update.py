"""Checking whether a newer collab has been released.

Two agents on different versions can disagree about the wire format, so the
moment to notice is when someone starts or joins a session — not at some random
later point. The check is cached, best-effort, and never blocks the thing you
actually asked for: no network, no GitHub, or no answer in time all mean "carry
on".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from . import __version__
from .config import global_config_path

RELEASES_API = "https://api.github.com/repos/rperez93/collab-a2a/releases/latest"
REPO_URL = "https://github.com/rperez93/collab-a2a"

#: Long enough that starting sessions all day costs one request.
CACHE_SECONDS = 6 * 3600
TIMEOUT = 4.0


def cache_path() -> Path:
    return global_config_path().parent / "update-check.json"


def _parse(version: str) -> tuple[int, ...]:
    cleaned = version.strip().lstrip("vV")
    parts: list[int] = []
    for chunk in cleaned.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts or [0])


def is_newer(candidate: str, current: str) -> bool:
    return _parse(candidate) > _parse(current)


@dataclass
class UpdateInfo:
    current: str
    latest: str = ""
    available: bool = False
    checked_at: float = 0.0
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "current": self.current,
            "latest": self.latest,
            "available": self.available,
            "checked_at": self.checked_at,
            "error": self.error,
        }


def read_cache() -> UpdateInfo | None:
    p = cache_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError):
        return None
    info = UpdateInfo(
        current=str(data.get("current") or __version__),
        latest=str(data.get("latest") or ""),
        available=bool(data.get("available")),
        checked_at=float(data.get("checked_at") or 0),
        error=str(data.get("error") or ""),
    )
    # A cached answer about an older build says nothing about this one.
    if info.current != __version__ and info.latest:
        info.available = is_newer(info.latest, __version__)
        info.current = __version__
    return info


def _write_cache(info: UpdateInfo) -> None:
    p = cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(info.to_dict()))
    except OSError:
        pass


def check(*, force: bool = False, timeout: float = TIMEOUT) -> UpdateInfo:
    """Return what we know about newer releases, consulting the cache first."""
    if os.environ.get("COLLAB_NO_UPDATE_CHECK") == "1":
        return UpdateInfo(current=__version__, error="disabled")

    cached = read_cache()
    if cached and not force and (time.time() - cached.checked_at) < CACHE_SECONDS:
        return cached

    info = UpdateInfo(current=__version__, checked_at=time.time())
    try:
        r = httpx.get(RELEASES_API, timeout=timeout,
                      headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 200:
            info.latest = str(r.json().get("tag_name") or "").lstrip("vV")
            info.available = bool(info.latest) and is_newer(info.latest, __version__)
        else:
            info.error = f"github returned {r.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        # Offline, rate-limited, behind a proxy — none of it is our problem.
        info.error = str(exc)[:120]
        if cached:
            info.latest, info.available = cached.latest, cached.available

    _write_cache(info)
    return info


def repo_dir() -> Path | None:
    """The checkout this collab runs from, if it is one we could update."""
    here = Path(__file__).resolve()
    candidate = here.parent.parent.parent  # src/collab/update.py -> repo root
    return candidate if (candidate / "install.sh").exists() else None


def apply_update() -> tuple[bool, str]:
    """Run `git pull` and `./install.sh` in the checkout. Returns (ok, output)."""
    repo = repo_dir()
    if repo is None:
        return False, (
            "collab is not running from a git checkout, so it cannot update itself.\n"
            f"Reinstall from {REPO_URL}"
        )
    try:
        pull = subprocess.run(["git", "-C", str(repo), "pull", "--ff-only"],
                              capture_output=True, text=True, timeout=120)
        if pull.returncode != 0:
            return False, (pull.stderr or pull.stdout).strip()
        install = subprocess.run([str(repo / "install.sh")], cwd=str(repo),
                                 capture_output=True, text=True, timeout=600)
        if install.returncode != 0:
            return False, (install.stderr or install.stdout).strip()[-2000:]
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return True, (pull.stdout or "").strip()


def prompt_and_maybe_update(info: UpdateInfo, *, assume_yes: bool = False) -> bool:
    """Offer the update when a human is there to answer. Returns True if applied.

    A non-interactive caller — which is most agents — is told and left alone
    rather than having its session start blocked on a question nobody will see.
    """
    if not info.available:
        return False

    banner = (f"collab {info.latest} is available (you have {info.current})")
    if not assume_yes and not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(f"  {banner} — update with: cd {repo_dir() or REPO_URL} && git pull && ./install.sh")
        return False

    if not assume_yes:
        print(f"  {banner}")
        try:
            answer = input("  update now? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return False
        if answer not in ("y", "yes"):
            return False

    print("  updating…")
    ok, output = apply_update()
    if not ok:
        print(f"  update failed: {output}")
        return False
    print(f"  updated to {info.latest} — re-run your command to use it")
    return True
