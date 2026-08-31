"""The installer must never damage a status line it did not create.

The last test here runs against a verbatim copy of a real machine's script,
which already hosts three other tools' segments.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from collab.statusline import install as sli

REAL_FIXTURE = Path(__file__).with_name("fixtures_statusline_real.sh")


@pytest.fixture()
def claude_home(tmp_path, monkeypatch):
    home = tmp_path / "claude"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    return home


def _settings(home: Path) -> dict:
    return json.loads((home / "settings.json").read_text())


def test_creates_script_when_nothing_configured(claude_home):
    result = sli.install_claude_code(executable="/opt/collab")
    assert result.action == "created"
    script = Path(_settings(claude_home)["statusLine"]["command"])
    body = script.read_text()
    assert body.startswith("#!/usr/bin/env bash")
    assert "input=$(cat)" in body
    assert sli.BEGIN in body and sli.END in body
    assert os.access(script, os.X_OK)
    assert _settings(claude_home)["statusLine"]["refreshInterval"] == 2


def test_appends_to_existing_script_at_the_top(claude_home):
    script = claude_home / "statusline-command.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "input=$(cat)\n"
        "# >>> OTHER-TOOL\n"
        'printf "other"\n'
        "# <<< OTHER-TOOL\n"
    )
    script.chmod(0o755)
    (claude_home / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": str(script)}})
    )

    result = sli.install_claude_code(executable="/opt/collab")
    assert result.action == "appended"
    body = script.read_text()
    # The other tool survives, and we come first.
    assert "# >>> OTHER-TOOL" in body and 'printf "other"' in body
    assert body.index(sli.BEGIN) < body.index("# >>> OTHER-TOOL")
    # And crucially, after the single stdin capture.
    assert body.index("input=$(cat)") < body.index(sli.BEGIN)
    assert result.backups and result.backups[0].exists()


def test_moves_an_inline_command_into_a_script(claude_home):
    inline = "jq -r '.model.display_name'"
    (claude_home / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": inline}})
    )
    result = sli.install_claude_code(executable="/opt/collab")
    assert result.action == "converted"
    body = result.script.read_text()
    assert inline in body, "the original inline command must be preserved verbatim"
    assert 'printf \'%s\' "$input" |' in body, "and still be fed the session JSON"
    assert body.index(sli.BEGIN) < body.index(inline)
    assert _settings(claude_home)["statusLine"]["command"] == str(result.script)


def test_install_is_idempotent(claude_home):
    sli.install_claude_code(executable="/opt/collab")
    script = Path(_settings(claude_home)["statusLine"]["command"])
    first = script.read_text()
    result = sli.install_claude_code(executable="/opt/collab")
    assert result.action == "updated"
    assert script.read_text().count(sli.BEGIN) == 1
    assert script.read_text() == first


def test_existing_refresh_interval_is_left_alone(claude_home):
    (claude_home / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": "echo hi", "refreshInterval": 30}})
    )
    sli.install_claude_code(executable="/opt/collab")
    assert _settings(claude_home)["statusLine"]["refreshInterval"] == 30


def test_uninstall_removes_only_our_block(claude_home):
    script = claude_home / "statusline-command.sh"
    original = (
        "#!/usr/bin/env bash\n"
        "input=$(cat)\n"
        "# >>> OTHER-TOOL\n"
        'printf "other"\n'
        "# <<< OTHER-TOOL\n"
    )
    script.write_text(original)
    (claude_home / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": str(script)}})
    )
    sli.install_claude_code(executable="/opt/collab")
    sli.uninstall_claude_code()
    assert script.read_text() == original, "uninstall must restore the file byte for byte"
    assert _settings(claude_home)["statusLine"]["command"] == str(script)


def test_uninstall_removes_a_script_we_created_outright(claude_home):
    sli.install_claude_code(executable="/opt/collab")
    script = Path(_settings(claude_home)["statusLine"]["command"])
    sli.uninstall_claude_code()
    assert not script.exists()
    assert "statusLine" not in _settings(claude_home)


@pytest.mark.skipif(not REAL_FIXTURE.exists(), reason="no real-world fixture captured")
def test_real_world_three_vendor_script_survives(claude_home):
    """Regression against an actual machine's script (Boost, local-tts, claude-statusline)."""
    script = claude_home / "statusline-command.sh"
    original = REAL_FIXTURE.read_text()
    script.write_text(original)
    script.chmod(0o755)
    (claude_home / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": str(script),
                                   "refreshInterval": 2, "padding": 0}})
    )

    sli.install_claude_code(executable="/opt/collab")
    body = script.read_text()
    for marker in ("BOOST-STATUS-LINE", "local-tts statusline hook", "claude-statusline"):
        assert marker in body, f"{marker} was lost"
    assert body.index(sli.BEGIN) < body.index("BOOST-STATUS-LINE")
    assert body.index("input=$(cat)") < body.index(sli.BEGIN)
    assert sli.status_claude_code()["installed"] is True

    sli.uninstall_claude_code()
    assert script.read_text() == original, "the real script must come back byte for byte"
    assert sli.status_claude_code()["installed"] is False


def test_appended_block_emits_no_trailing_separator(claude_home):
    """Other vendors' blocks prefix their own separator.

    Appending one here left a dangling ' · ' at the end of the status line.
    """
    script = claude_home / "statusline-command.sh"
    script.write_text("#!/usr/bin/env bash\ninput=$(cat)\n"
                      "# >>> OTHER\nprintf ' · other'\n# <<< OTHER\n")
    (claude_home / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": str(script)}}))
    sli.install_claude_code(executable="/opt/collab")
    block = script.read_text().split(sli.BEGIN)[1].split(sli.END)[0]
    assert "printf ' · '" not in block


def test_converted_inline_command_keeps_a_separator(claude_home):
    """A moved inline command prints no separator of its own, so we supply one."""
    (claude_home / "settings.json").write_text(
        json.dumps({"statusLine": {"type": "command", "command": "echo hi"}}))
    result = sli.install_claude_code(executable="/opt/collab")
    block = result.script.read_text().split(sli.BEGIN)[1].split(sli.END)[0]
    assert "printf ' · '" in block
