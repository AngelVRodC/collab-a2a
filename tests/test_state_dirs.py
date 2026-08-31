"""A state directory per agent, in one checkout.

Two agents in the same repo collide over collab's state — one profile, one
listener, one inbox, one lock — and nothing else. So that is the only thing
separated: `.collab-bob` beside `.collab`, same working tree, same files.

The hard part is not creating it. It is that a *later* command — `collab send`,
run as a fresh process minutes afterwards — has to find the same directory
again, with nothing carried over from the join.
"""

from __future__ import annotations

import os

import pytest

from collab import lockfile
from collab.config import (
    COLLAB_DIRNAME,
    agent_home,
    base_home,
    resolve_home,
    safe_slug,
    sibling_homes,
)


@pytest.fixture(autouse=True)
def repo(tmp_path, monkeypatch):
    monkeypatch.delenv("COLLAB_HOME", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _claim(home, name="alice", session_id="s_1", pid=None):
    home.mkdir(parents=True, exist_ok=True)
    lockfile.acquire(lockfile.Lock(name=name, session_id=session_id,
                                   hub_pid=pid or os.getpid()), home)


# --- naming ------------------------------------------------------------------

def test_the_directory_is_named_after_the_agent(repo):
    assert agent_home("bob") == repo / ".collab-bob"


def test_a_name_that_would_escape_the_repo_cannot(repo):
    home = agent_home("../../etc")
    assert home.parent == repo
    assert ".." not in home.name


@pytest.mark.parametrize("name,slug", [
    ("bob", "bob"), ("Bob Smith", "Bob-Smith"), ("agent/2", "agent-2"),
    ("", "agent"), ("...", "agent"),
])
def test_slugs(name, slug):
    assert safe_slug(name) == slug


# --- choosing one ------------------------------------------------------------

def test_an_empty_repo_uses_the_default(repo):
    assert resolve_home("bob") == repo / COLLAB_DIRNAME


def test_a_repo_held_by_someone_else_sends_us_beside_it(repo):
    _claim(base_home(), name="alice")
    assert resolve_home("bob") == repo / ".collab-bob"


def test_our_own_claim_is_not_someone_else(repo):
    """Re-running join for a session we are already in is not a collision."""
    _claim(base_home(), name="bob")
    assert resolve_home("bob") == base_home()


def test_a_stale_claim_does_not_displace_us(repo, monkeypatch):
    _claim(base_home(), name="alice", pid=999999)
    monkeypatch.setattr(os, "kill", _gone)
    assert resolve_home("bob") == base_home()


def test_an_explicit_home_still_wins(repo, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", "/somewhere/else")
    from collab.config import collab_home

    _claim(base_home(), name="alice")
    assert str(collab_home("bob")) == "/somewhere/else"


# --- finding it again, with no name to go on --------------------------------

def test_a_later_command_finds_the_one_live_sibling(repo):
    """`collab send` knows nothing about the join that came before it."""
    _claim(base_home(), name="alice")
    _claim(repo / ".collab-bob", name="bob", session_id="s_1")

    assert resolve_home() == repo / ".collab-bob"


def test_a_dead_sibling_is_not_mistaken_for_ours(repo, monkeypatch):
    live, dead = os.getpid(), 999999

    def selective(pid, sig):
        if pid == dead:
            raise ProcessLookupError

    monkeypatch.setattr(os, "kill", selective)
    _claim(base_home(), name="alice", pid=live)
    _claim(repo / ".collab-gone", name="carol", pid=dead)

    assert resolve_home() == base_home(), "no live sibling; fall back"


def test_with_two_live_siblings_a_name_is_required(repo):
    """Three agents is genuinely ambiguous, so it does not guess."""
    _claim(base_home(), name="alice")
    _claim(repo / ".collab-bob", name="bob")
    _claim(repo / ".collab-carol", name="carol")

    assert resolve_home() == base_home(), "ambiguous — do not pick one at random"
    assert resolve_home("carol") == repo / ".collab-carol", "with a name it is clear"


def test_siblings_are_listed(repo):
    _claim(base_home())
    _claim(repo / ".collab-bob")
    _claim(repo / ".collab-carol")

    names = {p.name for p in sibling_homes()}
    assert names == {".collab-bob", ".collab-carol"}
    assert COLLAB_DIRNAME not in names, "the default is not its own sibling"


# --- it is git-invisible -----------------------------------------------------

def test_a_new_directory_ignores_itself(repo):
    from collab.config import ensure_home

    home = ensure_home(name="bob")
    assert (home / ".gitignore").read_text().strip().endswith("*")


def _gone(pid, sig):
    raise ProcessLookupError
