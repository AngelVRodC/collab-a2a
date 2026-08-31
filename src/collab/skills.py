"""Installing collab's agent skills into a coding agent.

The skills live in ``src/collab/skills/`` and ship inside the package, so they
travel with an install rather than only existing in a checkout — there is one
copy, not a repo copy and a packaged copy drifting apart.

Installation is a symlink by default, so a skill edited in a checkout is live
immediately, with a copy as the fallback where linking is unavailable.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

SKILL_NAMES = ("collab-host", "collab-join", "collab-watch")


def bundled_skills_dir() -> Path | None:
    """Locate the shipped skills, whether installed or running from a checkout."""
    bundled = Path(__file__).resolve().parent / "skills"
    return bundled if (bundled / "collab-host" / "SKILL.md").exists() else None


def claude_skills_dir() -> Path:
    base = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    return base / "skills"


@dataclass
class SkillResult:
    installed: list[str]
    skipped: list[str]
    target: Path
    linked: bool


def install(*, target: Path | None = None, copy: bool = False,
            force: bool = False) -> SkillResult:
    source = bundled_skills_dir()
    if source is None:
        raise RuntimeError("could not find collab's bundled skills")

    dest_root = target or claude_skills_dir()
    dest_root.mkdir(parents=True, exist_ok=True)

    installed, skipped, linked_any = [], [], False
    for name in SKILL_NAMES:
        src = source / name
        if not (src / "SKILL.md").exists():
            continue
        dest = dest_root / name

        if dest.exists() or dest.is_symlink():
            # Never clobber something we did not put there.
            ours = dest.is_symlink() and Path(os.readlink(dest)).resolve() == src.resolve()
            if not (force or ours):
                skipped.append(name)
                continue
            if dest.is_symlink() or dest.is_file():
                dest.unlink()
            else:
                shutil.rmtree(dest)

        if copy:
            shutil.copytree(src, dest)
        else:
            try:
                dest.symlink_to(src, target_is_directory=True)
                linked_any = True
            except OSError:
                # Windows without developer mode, or a filesystem that cannot link.
                shutil.copytree(src, dest)
        installed.append(name)

    return SkillResult(installed, skipped, dest_root, linked_any)


def uninstall(*, target: Path | None = None) -> SkillResult:
    dest_root = target or claude_skills_dir()
    removed, skipped = [], []
    source = bundled_skills_dir()

    for name in SKILL_NAMES:
        dest = dest_root / name
        if not (dest.exists() or dest.is_symlink()):
            continue
        # Only remove what we installed: a link to our copy, or a directory
        # whose SKILL.md still carries our name.
        ours = dest.is_symlink() and source is not None and \
            Path(os.readlink(dest)).resolve() == (source / name).resolve()
        if not ours and dest.is_dir():
            skill_md = dest / "SKILL.md"
            ours = skill_md.exists() and f"name: {name}" in skill_md.read_text()
        if not ours:
            skipped.append(name)
            continue
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        else:
            shutil.rmtree(dest)
        removed.append(name)

    return SkillResult(removed, skipped, dest_root, False)


def status(*, target: Path | None = None) -> dict[str, object]:
    dest_root = target or claude_skills_dir()
    out: dict[str, object] = {"target": str(dest_root), "skills": {}}
    for name in SKILL_NAMES:
        dest = dest_root / name
        if dest.is_symlink():
            state = f"linked -> {os.readlink(dest)}"
        elif dest.is_dir():
            state = "copied"
        else:
            state = "not installed"
        out["skills"][name] = state  # type: ignore[index]
    return out
