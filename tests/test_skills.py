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
    result = sk.install(target=target)[0]
    assert sorted(result.installed) == sorted(sk.SKILL_NAMES)
    for name in sk.SKILL_NAMES:
        assert (target / name / "SKILL.md").exists()


def test_install_is_idempotent(target):
    sk.install(target=target)
    again = sk.install(target=target)[0]
    assert sorted(again.installed) == sorted(sk.SKILL_NAMES)
    assert not again.skipped


def test_a_foreign_skill_of_the_same_name_is_never_clobbered(target):
    """Someone else's skill is not ours to overwrite."""
    theirs = target / "collab-host"
    theirs.mkdir()
    (theirs / "SKILL.md").write_text("---\nname: someone-elses\n---\nmine\n")

    result = sk.install(target=target)[0]
    assert "collab-host" in result.skipped
    assert (theirs / "SKILL.md").read_text() == "---\nname: someone-elses\n---\nmine\n"

    forced = sk.install(target=target, force=True)[0]
    assert "collab-host" in forced.installed


def test_uninstall_removes_only_ours(target):
    foreign = target / "unrelated"
    foreign.mkdir()
    (foreign / "SKILL.md").write_text("---\nname: unrelated\n---\n")

    sk.install(target=target)
    removed = sk.uninstall(target=target)[0]

    assert sorted(removed.installed) == sorted(sk.SKILL_NAMES)
    assert (foreign / "SKILL.md").exists(), "an unrelated skill must survive"


def test_copy_mode_produces_real_files(target):
    result = sk.install(target=target, copy=True)[0]
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


# --- every agent on the machine, not just Claude Code ------------------------

def test_it_knows_more_than_one_agent():
    keys = {t.key for t in sk.known_targets()}
    assert {"claude-code", "codex", "gemini", "opencode", "cursor"} <= keys


def test_only_agents_that_are_actually_here_are_detected(tmp_path, monkeypatch):
    """Writing into a directory an agent does not have would just be litter."""
    monkeypatch.setenv("COLLAB_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nope"))
    assert sk.detect_targets() == []

    (tmp_path / ".codex").mkdir()
    assert [t.key for t in sk.detect_targets()] == ["codex"]


def test_a_single_file_agent_gets_a_short_block(tmp_path, monkeypatch):
    """Its file is read on every prompt, so it gets a pointer, not four skills.

    Only agents with no skill support are written this way now.
    """
    monkeypatch.setenv("COLLAB_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nope"))
    (tmp_path / ".config" / "crush").mkdir(parents=True)

    results = sk.install()
    assert len(results) == 1

    body = (tmp_path / ".config" / "crush" / "AGENTS.md").read_text()
    assert sk.BEGIN in body and sk.END in body
    assert "collab host" in body
    assert len(body.splitlines()) < 60, "this is read on every prompt; keep it short"


def test_an_existing_instructions_file_is_not_disturbed(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nope"))
    (tmp_path / ".config" / "crush").mkdir(parents=True)
    theirs = tmp_path / ".config" / "crush" / "AGENTS.md"
    theirs.write_text("# My rules\n\nAlways run the tests.\n")

    sk.install()
    body = theirs.read_text()
    assert "Always run the tests." in body, "their instructions must survive"
    assert body.index("My rules") < body.index(sk.BEGIN), "ours goes after theirs"


def test_installing_twice_leaves_one_block(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nope"))
    (tmp_path / ".config" / "crush").mkdir(parents=True)

    sk.install()
    sk.install()
    body = (tmp_path / ".config" / "crush" / "AGENTS.md").read_text()
    assert body.count(sk.BEGIN) == 1


def test_uninstall_leaves_their_instructions_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nope"))
    (tmp_path / ".config" / "crush").mkdir(parents=True)
    theirs = tmp_path / ".config" / "crush" / "AGENTS.md"
    theirs.write_text("# My rules\n\nAlways run the tests.\n")

    sk.install()
    sk.uninstall()

    assert theirs.read_text().strip() == "# My rules\n\nAlways run the tests.".strip()


def test_a_file_that_held_only_our_block_is_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "nope"))
    (tmp_path / ".codex").mkdir()

    sk.install()
    sk.uninstall()
    assert not (tmp_path / ".codex" / "AGENTS.md").exists()


def test_an_unknown_agent_name_is_rejected_helpfully():
    with pytest.raises(RuntimeError, match="unknown agent"):
        sk.install(agent="notanagent")
