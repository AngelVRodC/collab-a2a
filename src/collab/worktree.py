"""Giving a second agent in the same repo a place of its own.

Session state is per repo — one ``.collab/`` at the repository root, holding one
profile, one daemon, one inbox. That is right for one agent per checkout and
wrong the moment two agents work in the same one: the second overwrites the
first's profile with its own name and token, both daemons write the same status
file, and ``stop_orphans`` reads the other's listener as a leftover and stops
it. Neither agent is told; the first simply goes quiet.

A git worktree fixes it at the root, because collab's notion of "repo" is the
git top level. A worktree is a different top level of the same repository, so
the second agent gets its own ``.collab/`` for free — and, being a real
checkout, its own files to edit, which is the other half of the problem two
agents in one directory have.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import SessionProfile, collab_home, repo_root
from .client.daemon import is_running


@dataclass
class Worktree:
    path: Path
    branch: str
    created: bool          # False when we reattached to one that already existed


def _git(repo: Path, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                          text=True, timeout=timeout, check=False)


def is_git_repo(path: Path | None = None) -> bool:
    path = Path(path or Path.cwd())
    out = _git(path, "rev-parse", "--git-dir", timeout=5.0)
    return out.returncode == 0


def has_commits(repo: Path) -> bool:
    """``git worktree add`` needs something to check out."""
    return _git(repo, "rev-parse", "--verify", "HEAD", timeout=5.0).returncode == 0


def occupant(home: Path | str | None = None) -> SessionProfile | None:
    """The agent already using this repo's collab state, if there is one.

    Liveness is a running listener, not a file on disk: a stopped session left
    a profile behind and that must not be read as somebody being here.
    """
    root = Path(home) if home else collab_home()

    # The lock is the explicit claim; prefer it, and let it stand in for a
    # profile when the holder is a hub with no listener of its own running.
    from . import lockfile

    held = lockfile.holder(root)
    if held is not None:
        found = SessionProfile.load(held.session_id, root)
        if found is not None:
            return found
        return SessionProfile(session_id=held.session_id, url=held.url,
                              name=held.name, host_name=held.name,
                              token="", home=str(root))

    sessions = root / "sessions"
    if not sessions.is_dir():
        return None
    for child in sorted(sessions.iterdir()):
        if not child.is_dir():
            continue
        profile = SessionProfile.load_from(child)
        if profile is not None and is_running(profile) is not None:
            return profile
    return None


def default_path(repo: Path, name: str) -> Path:
    """A sibling of the repo, which is where git worktrees conventionally live.

    Not inside the repo: a checkout nested in another checkout confuses every
    tool that walks a directory tree, and the agent working here should see an
    ordinary repository.
    """
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in name)
    return repo.parent / f"{repo.name}-{safe or 'collab'}"


def branch_name(name: str) -> str:
    safe = "".join(ch if (ch.isalnum() or ch in "-_") else "-" for ch in name)
    return f"collab/{safe or 'agent'}"


def existing(repo: Path, path: Path) -> bool:
    """Is this path already a worktree of this repository?"""
    out = _git(repo, "worktree", "list", "--porcelain")
    if out.returncode != 0:
        return False
    target = str(path.resolve())
    for line in out.stdout.splitlines():
        if line.startswith("worktree "):
            try:
                if str(Path(line[9:]).resolve()) == target:
                    return True
            except OSError:
                continue
    return False


def create(repo: Path, name: str, path: Path | None = None) -> Worktree:
    """Add a worktree for this agent, reusing one that is already there.

    Raises ``RuntimeError`` with something a person can act on. Creating a
    branch is a visible change to someone's repository, so nothing here is
    silent and everything it makes is named after collab.
    """
    repo = Path(repo).resolve()
    path = Path(path) if path else default_path(repo, name)
    branch = branch_name(name)

    if not is_git_repo(repo):
        raise RuntimeError(
            f"{repo} is not a git repository, so there is no worktree to make")
    if not has_commits(repo):
        raise RuntimeError(
            "this repository has no commits yet, and a worktree needs something"
            " to check out — make one commit first")

    if existing(repo, path):
        return Worktree(path=path, branch=branch, created=False)
    if path.exists() and any(path.iterdir()):
        raise RuntimeError(f"{path} already exists and is not a worktree of this repo")

    # A fresh branch off the current HEAD is the common case.
    out = _git(repo, "worktree", "add", "-b", branch, str(path))
    if out.returncode == 0:
        return Worktree(path=path, branch=branch, created=True)

    # The branch is already there from a previous run — check it out instead.
    retry = _git(repo, "worktree", "add", str(path), branch)
    if retry.returncode == 0:
        return Worktree(path=path, branch=branch, created=False)

    # It exists *and* is checked out somewhere else. Detached still gives the
    # agent its own files and its own .collab, which is what this is for.
    detached = _git(repo, "worktree", "add", "--detach", str(path))
    if detached.returncode == 0:
        return Worktree(path=path, branch="(detached)", created=True)

    raise RuntimeError((out.stderr or out.stdout or "git worktree add failed").strip())


def remove(repo: Path, path: Path, *, force: bool = False) -> bool:
    args = ["worktree", "remove", str(path)] + (["--force"] if force else [])
    return _git(Path(repo), *args).returncode == 0


def describe(worktree: Worktree, occupied_by: str) -> list[str]:
    """What to tell the user, in the order they need it."""
    lines = [
        f"{occupied_by} is already using this repo's collab state,"
        " so this session runs from a worktree",
        f"  path   {worktree.path}",
        f"  branch {worktree.branch}",
    ]
    if worktree.created:
        lines.append("  work there, not in the original checkout —"
                     " that is the point of the split")
    else:
        lines.append("  reattached to the worktree from an earlier run")
    return lines
