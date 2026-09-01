"""Each agent gets the shape it actually expects.

`SKILL.md` started as Claude Code's and became an open standard: a folder per
skill, loaded when relevant rather than on every prompt. Codex, Gemini CLI,
Cursor, opencode and Antigravity all read it now. collab sent every one of them
a block in an instructions file instead, which was right when it was written
and is not any more.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from collab import skills as sk


@pytest.fixture(autouse=True)
def agent_home(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_AGENT_HOME", str(tmp_path))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    return tmp_path


def _targets():
    return {t.key: t for t in sk.known_targets()}


# --- where each agent is written --------------------------------------------

@pytest.mark.parametrize("key,tail", [
    ("claude-code", ".claude/skills"),
    ("codex", ".codex/skills"),
    ("gemini", ".gemini/skills"),
    ("antigravity", ".gemini/config/skills"),
    ("opencode", ".config/opencode/skills"),
    ("cursor", ".cursor/skills"),
    ("agents-std", ".agents/skills"),
])
def test_skill_agents_get_a_skills_directory(key, tail):
    target = _targets()[key]
    assert target.kind == "skills", f"{key} takes real skills now"
    assert str(target.path).endswith(tail)


@pytest.mark.parametrize("key", ["amp", "crush", "goose", "windsurf"])
def test_agents_without_skill_support_still_get_the_short_block(key):
    assert _targets()[key].kind == "file"


def test_the_skills_installed_carry_the_frontmatter_the_standard_requires(agent_home):
    """`name` must match the folder, and `description` is what an agent reads
    to decide whether the skill is relevant."""
    (agent_home / ".codex").mkdir()
    sk.install(copy=True)

    for name in sk.SKILL_NAMES:
        text = (agent_home / ".codex" / "skills" / name / "SKILL.md").read_text()
        head = text.split("---")[1]
        assert f"name: {name}" in head, "the name must match its folder"
        assert "description:" in head


# --- graduating from the old shape ------------------------------------------

def test_the_old_block_is_removed_when_skills_go_in(agent_home):
    """Left behind, it repeats on every prompt what the skills now say only
    when they matter — and only one of the two would ever be updated."""
    codex = agent_home / ".codex"
    codex.mkdir()
    agents_md = codex / "AGENTS.md"
    agents_md.write_text("# My rules\n\nAlways run the tests.\n\n"
                         + sk.instructions_block(Path("/x"), "collab") + "\n")

    sk.install()

    body = agents_md.read_text()
    assert sk.BEGIN not in body, "our block is gone"
    assert "Always run the tests." in body, "theirs is untouched"
    assert (codex / "skills" / "collab-host" / "SKILL.md").exists()


def test_a_file_that_was_only_ours_is_taken_away_with_it(agent_home):
    codex = agent_home / ".codex"
    codex.mkdir()
    agents_md = codex / "AGENTS.md"
    agents_md.write_text(sk.instructions_block(Path("/x"), "collab") + "\n")

    sk.install()
    assert not agents_md.exists(), "an empty file we created is litter"


def test_an_agent_with_no_old_block_is_not_touched(agent_home):
    codex = agent_home / ".codex"
    codex.mkdir()
    agents_md = codex / "AGENTS.md"
    agents_md.write_text("# Only mine\n")

    sk.install()
    assert agents_md.read_text() == "# Only mine\n"


# --- the shared directory ----------------------------------------------------

def test_the_shared_directory_is_only_used_when_it_exists(agent_home):
    """Creating ~/.agents would install collab into agents that never asked."""
    assert "agents-std" not in {t.key for t in sk.detect_targets()}

    (agent_home / ".agents").mkdir()
    assert "agents-std" in {t.key for t in sk.detect_targets()}


def test_agents_reading_the_shared_directory_are_not_written_twice(agent_home):
    """Cursor, opencode and Gemini read both. Two copies of one skill, loaded
    from two places, is worse than either alone."""
    for d in (".agents", ".cursor", ".config/opencode", ".gemini"):
        (agent_home / d).mkdir(parents=True)

    keys = {t.key for t in sk.detect_targets()}
    assert "agents-std" in keys
    assert not ({"cursor", "opencode", "gemini"} & keys)


def test_codex_keeps_its_own_even_with_the_shared_one(agent_home):
    """Codex reads ~/.codex/skills; it is not one of the three."""
    (agent_home / ".agents").mkdir()
    (agent_home / ".codex").mkdir()

    keys = {t.key for t in sk.detect_targets()}
    assert {"agents-std", "codex"} <= keys


# --- antigravity's several homes ---------------------------------------------

def test_antigravity_is_found_by_any_of_its_flavours(agent_home):
    (agent_home / ".gemini" / "antigravity-cli").mkdir(parents=True)
    keys = {t.key for t in sk.detect_targets()}
    assert "antigravity" in keys
