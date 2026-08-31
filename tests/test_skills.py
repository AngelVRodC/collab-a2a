"""Installing the agent skills: never clobber, always removable."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from collab import skills as sk


@pytest.fixture()
def target(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    return d


def test_skills_ship_inside_the_package():
    """They must survive an install, not only exist in a checkout."""
    bundled = sk.bundled_skills_dir()
    assert bundled is not None
    assert bundled.name == "skills" and bundled.parent.name == "collab"
    for name in sk.SKILL_NAMES:
        text = (bundled / name / "SKILL.md").read_text()
        assert text.startswith("---"), f"{name} needs YAML frontmatter to be discoverable"
        assert f"name: {name}" in text
        assert "description:" in text


def test_install_links_every_skill(target):
    result = sk.install(target=target)
    assert sorted(result.installed) == sorted(sk.SKILL_NAMES)
    for name in sk.SKILL_NAMES:
        assert (target / name / "SKILL.md").exists()


def test_install_is_idempotent(target):
    sk.install(target=target)
    again = sk.install(target=target)
    assert sorted(again.installed) == sorted(sk.SKILL_NAMES)
    assert not again.skipped


def test_a_foreign_skill_of_the_same_name_is_never_clobbered(target):
    """Someone else's skill is not ours to overwrite."""
    theirs = target / "collab-host"
    theirs.mkdir()
    (theirs / "SKILL.md").write_text("---\nname: someone-elses\n---\nmine\n")

    result = sk.install(target=target)
    assert "collab-host" in result.skipped
    assert (theirs / "SKILL.md").read_text() == "---\nname: someone-elses\n---\nmine\n"

    forced = sk.install(target=target, force=True)
    assert "collab-host" in forced.installed


def test_uninstall_removes_only_ours(target):
    foreign = target / "unrelated"
    foreign.mkdir()
    (foreign / "SKILL.md").write_text("---\nname: unrelated\n---\n")

    sk.install(target=target)
    removed = sk.uninstall(target=target)

    assert sorted(removed.installed) == sorted(sk.SKILL_NAMES)
    assert (foreign / "SKILL.md").exists(), "an unrelated skill must survive"


def test_copy_mode_produces_real_files(target):
    result = sk.install(target=target, copy=True)
    assert result.linked is False
    for name in sk.SKILL_NAMES:
        assert not (target / name).is_symlink()
        assert (target / name / "SKILL.md").is_file()


def test_status_reports_what_is_there(target):
    before = sk.status(target=target)
    assert all(v == "not installed" for v in before["skills"].values())
    sk.install(target=target)
    after = sk.status(target=target)
    assert all(v != "not installed" for v in after["skills"].values())
