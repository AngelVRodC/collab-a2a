"""The themes folder: one hand-written Markdown file per theme.

The risk with this format is not that it fails to read the settings — that shows
up immediately. It is that it reads too much: a file where you explain why you
made the theme is full of sentences with colons, and if one of them slips in as
a setting the theme does something nobody wrote. Most of these tests are about
that.
"""
from __future__ import annotations

import pytest

from collab import config, themes


@pytest.fixture
def folder(tmp_path):
    d = tmp_path / "themes"
    d.mkdir()
    themes._MD_CACHE.clear()
    yield d
    themes._MD_CACHE.clear()


def write(d, name, text):
    (d / f"{name}.md").write_text(text, encoding="utf-8")


# --- what does get read ------------------------------------------------------

def test_the_front_matter_defines_the_theme(folder):
    write(folder, "mine", "---\nlayout: bubbles\nfold: 6\nown_side: left\n---\n")
    r = themes.resolve("mine", folder=folder)
    assert r["fold"] == 6
    assert r["own_side"] == "left"
    assert r["layout"] == "bubbles"          # inherited from chat


def test_a_block_marked_theme_counts_too(folder):
    write(folder, "mine", "# Notes\n\n```theme\nfold: 9\n```\n")
    assert themes.resolve("mine", folder=folder)["fold"] == 9


def test_the_theme_name_is_the_file_name(folder):
    write(folder, "My-Theme", "---\nfold: 1\n---\n")
    assert "my-theme" in themes.all_themes(folder=folder)


def test_types_are_guessed(folder):
    write(folder, "t", '---\nfold: 3\nbubble_share: 0.75\n'
                       'tones: false\ngroup_by_author: yes\nchars: "++++=|"\n---\n')
    r = themes.resolve("t", folder=folder)
    assert r["fold"] == 3 and isinstance(r["fold"], int)
    assert r["bubble_share"] == 0.75
    assert r["tones"] is False
    assert r["group_by_author"] is True
    assert r["chars"] == "++++=|"            # the quotes are stripped


# --- what does NOT get read: the prose --------------------------------------

def test_the_prose_is_never_interpreted(folder):
    """This is what makes the format usable, and the easiest thing to break."""
    write(folder, "mine", """---
layout: bubbles
fold: 4
---

# My theme

Note: the red is too loud.
Todo: try fold 8.
frame: this is prose and must not be applied
own_side: nor this

One day I will try `text: $GOOD` again.
""")
    r = themes.resolve("mine", folder=folder)
    assert r["fold"] == 4
    # The default, not the prose's. Which default it is does not matter here;
    # what matters is that the line below the settings block did not become a
    # setting.
    assert r["frame"] == themes.DEFAULTS["frame"]
    assert r["own_side"] == "right"
    assert themes.load_md_themes(folder)[1] == [], "it warned about the prose"


def test_a_file_with_no_settings_is_still_a_theme(folder):
    """Writing only prose must not leave you without a theme, or full of warnings."""
    write(folder, "empty", "# A theme\n\nI have not decided anything yet.\n")
    assert "empty" in themes.all_themes(folder=folder)
    assert themes.resolve("empty", folder=folder)["layout"] == "bubbles"


# --- typos: reported, never swallowed ---------------------------------------

def test_an_unknown_key_is_named(folder):
    write(folder, "mine", "---\nfram: $GOOD\n---\n")
    _, warnings = themes.load_md_themes(folder)
    assert warnings and "fram" in warnings[0]


def test_a_loosely_punctuated_key_is_understood(folder):
    """`own side` and `own-side` are the same setting as `own_side`."""
    write(folder, "a", "---\nown side: left\n---\n")
    write(folder, "b", "---\nOwn-Side: left\n---\n")
    assert themes.resolve("a", folder=folder)["own_side"] == "left"
    assert themes.resolve("b", folder=folder)["own_side"] == "left"


def test_a_missing_folder_is_not_an_error(folder):
    assert themes.load_md_themes(folder / "not-there") == ({}, [])


# --- the folder wins, and is re-read when it changes ------------------------

def test_the_folder_wins_over_the_built_ins(folder):
    """A file named after a built-in theme replaces it.

    It is what anyone who just wrote it expects: if your chat.md says fold 7,
    the chat folds at 7.
    """
    write(folder, "chat", "---\nlayout: bubbles\nfold: 7\n---\n")
    assert themes.resolve("chat", folder=folder)["fold"] == 7
    assert themes.resolve("chat", folder=folder)["layout"] == "bubbles"


def test_editing_the_file_is_seen_without_restarting(folder):
    write(folder, "mine", "---\nfold: 2\n---\n")
    assert themes.resolve("mine", folder=folder)["fold"] == 2
    write(folder, "mine", "---\nfold: 8\n---\n")
    assert themes.resolve("mine", folder=folder)["fold"] == 8


def test_the_folder_is_not_re_read_when_nothing_changed(folder, monkeypatch):
    """The viewer asks for the theme on every frame."""
    write(folder, "mine", "---\nfold: 2\n---\n")
    themes.load_md_themes(folder)
    reads = []
    from pathlib import Path as _P
    real = _P.read_text
    monkeypatch.setattr(_P, "read_text",
                        lambda self, *a, **k: (reads.append(1),
                                               real(self, *a, **k))[1])
    for _ in range(15):
        themes.load_md_themes(folder)
    assert reads == []


# --- it can be chosen, and survives closing the session ---------------------

def test_a_folder_theme_can_be_chosen_and_persists(folder, tmp_path, monkeypatch):
    """What is stored is global, not per session: a theme is how *you* read.

    If it lived in the session you would drop back to the built-in every time
    you joined a new one — and whoever set it once takes it as done.
    """
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(config, "global_config_path", lambda: cfg)
    monkeypatch.setattr(themes, "user_themes_dir", lambda home=None: folder)
    config._CACHE.clear()
    write(folder, "mine", "---\nfold: 5\n---\n")

    assert config.set_theme("mine") == "mine"
    config._CACHE.clear()                    # as if freshly started
    assert config.theme() == "mine"
    assert "mine" in config.theme_names()
