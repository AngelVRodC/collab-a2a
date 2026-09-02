"""Ways a theme file can be wrong that used to cost you the whole chat.

All five were found by running them, not by reading the code, and four of them
were silent or fatal rather than merely wrong. They are together because they
share a shape: a configuration file is written by a person, on a machine you do
not control, with an editor you did not choose — so "the file is malformed" is a
normal Tuesday, not an exceptional case.
"""
from __future__ import annotations

from collab import themes
from collab.client import tui


# --- a file that is not UTF-8 -----------------------------------------------

def test_a_latin1_file_is_reported_and_the_viewer_lives(folder):
    """UnicodeDecodeError is a ValueError, not an OSError.

    Every other file problem — a directory, a broken symlink, no permission —
    was handled; this one came straight out of the draw loop. A file saved as
    latin-1 or UTF-16 (PowerShell's `>` writes UTF-16) took down the chat,
    `collab theme`, and `collab theme --check` — the one tool whose job is to
    tell you what is wrong with your theme.
    """
    (folder / "bad.md").write_bytes(
        "---\nfold: 6\n---\n\nNotación: el rojo\n".encode("latin-1"))
    (folder / "good.md").write_text("---\nfold: 2\n---\n", encoding="utf-8")

    settings, warnings = themes.load_md_themes(folder)
    assert any("bad.md" in w and "UTF-8" in w for w in warnings)
    assert settings.get("good", {}).get("fold") == 2, "one bad file lost the rest"
    assert themes.resolve("good", folder)["fold"] == 2


def test_a_file_that_is_not_utf8_does_not_kill_the_draw_loop(folder, monkeypatch):
    (folder / "bad.md").write_bytes("---\nfold: 6\n---\n".encode("utf-16"))
    monkeypatch.setattr(tui, "theme", lambda: "chat")
    tui._THEME_CACHE.clear()
    assert tui._current_theme()["layout"] == "bubbles"


# --- a byte-order mark -------------------------------------------------------

def test_a_bom_does_not_void_the_theme(folder):
    """`\\ufeff` is not whitespace to str.strip().

    So the first line read as '\\ufeff---', the front matter was never found,
    and every setting silently fell back to its default with no warning at all —
    the worst way a configuration file can fail. Notepad, VS Code's "UTF-8 with
    BOM" and PowerShell all produce one without being asked.
    """
    (folder / "bom.md").write_text(
        "﻿---\nlayout: log\nfold: 0\ntones: false\n---\n", encoding="utf-8")
    r = themes.resolve("bom", folder)
    assert r["layout"] == "log"
    assert r["fold"] == 0
    assert r["tones"] is False


# --- a block that is never closed -------------------------------------------

def test_an_unclosed_front_matter_takes_nothing_and_says_so(folder):
    """This is the exact thing rule 2 exists to prevent.

    With no closing `---` the whole document was read as settings, so a line of
    prose like «text: I meant something else» quietly changed the body colour —
    and the warning named the harmless line while staying silent about the one
    that landed.
    """
    (folder / "open.md").write_text("""---
fold: 6

# My theme

Note: the red is too loud
text: I meant something else
""", encoding="utf-8")
    settings, warnings = themes.load_md_themes(folder)
    assert settings["open"] == {}, f"it read {settings['open']}"
    assert any("never closed" in w for w in warnings), warnings


def test_an_unclosed_theme_block_takes_nothing_and_says_so(folder):
    (folder / "open2.md").write_text(
        "# Notes\n\n```theme\nfold: 5\n\nAnd then I forgot to close it.\n"
        "text: red\n", encoding="utf-8")
    settings, warnings = themes.load_md_themes(folder)
    assert settings["open2"] == {}
    assert any("never closed" in w for w in warnings), warnings


def test_a_properly_closed_block_still_works(folder):
    """The control: the fix must not cost the case that was fine."""
    (folder / "ok.md").write_text("# Notes\n\n```theme\nfold: 5\n```\n\nProse.\n",
                                  encoding="utf-8")
    assert themes.resolve("ok", folder)["fold"] == 5


# --- editing a file has to reach an open viewer ------------------------------

def test_saving_over_an_existing_file_reaches_the_viewer(folder, monkeypatch):
    """The stamp used to come from the FOLDER, whose mtime does not move.

    Saving an existing file leaves the directory untouched, so editing your
    theme did nothing until you happened to create another one — with nano,
    `cat >`, or any editor that writes in place. Which is precisely what
    `_current_theme`'s own docstring promised would work.
    """
    (folder / "mine.md").write_text("---\nfold: 3\n---\n", encoding="utf-8")
    monkeypatch.setattr(tui, "theme", lambda: "mine")
    tui._THEME_CACHE.clear()
    assert tui._current_theme()["fold"] == 3

    (folder / "mine.md").write_text("---\nfold: 9\n---\n", encoding="utf-8")
    assert tui._current_theme()["fold"] == 9, "the viewer kept the old value"


def test_the_theme_is_not_re_resolved_when_nothing_changed(folder, monkeypatch):
    """The other half: this runs on every frame."""
    (folder / "mine.md").write_text("---\nfold: 3\n---\n", encoding="utf-8")
    monkeypatch.setattr(tui, "theme", lambda: "mine")
    tui._THEME_CACHE.clear()
    tui._current_theme()

    calls = []
    real = themes.resolve
    monkeypatch.setattr(themes, "resolve",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    for _ in range(20):
        tui._current_theme()
    assert calls == []
