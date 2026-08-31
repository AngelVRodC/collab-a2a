"""Two agents in one checkout.

State is per repo, so a second agent joining from the same directory reuses the
first's `.collab/`: it overwrites the profile with its own name and token, both
daemons write the same status file, and `stop_orphans` reads the other's
listener as a leftover and stops it. Nobody is told; the first agent just goes
quiet.

A worktree is a different git top level of the same repository, which is
exactly the boundary collab keys state on — so the second agent gets its own
`.collab/`, and its own files to edit.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from collab import worktree as wt
from collab.config import SessionProfile


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=path, check=True)
    return path


@pytest.fixture()
def repo(tmp_path):
    return _repo(tmp_path / "proj")


# --- detecting that someone is already here ---------------------------------

def test_an_empty_repo_is_free(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_HOME", str(tmp_path / ".collab"))
    assert wt.occupant() is None


def test_a_stopped_session_does_not_count_as_an_occupant(tmp_path, monkeypatch):
    """A profile on disk is a session that existed, not an agent that is here."""
    home = tmp_path / ".collab"
    monkeypatch.setenv("COLLAB_HOME", str(home))
    d = home / "sessions" / "s_old"
    d.mkdir(parents=True)
    SessionProfile(session_id="s_old", url="u", name="alice", host_name="alice",
                   token="t", home=str(home)).save()
    assert wt.occupant() is None, "nobody is listening; the repo is free"


def test_a_running_listener_is_an_occupant(tmp_path, monkeypatch):
    home = tmp_path / ".collab"
    monkeypatch.setenv("COLLAB_HOME", str(home))
    profile = SessionProfile(session_id="s_live", url="u", name="alice",
                             host_name="alice", token="t", home=str(home))
    profile.dir.mkdir(parents=True)
    profile.save()
    (profile.dir / "daemon.pid").write_text(str(os.getpid()))

    found = wt.occupant()
    assert found is not None and found.name == "alice"


# --- making the worktree -----------------------------------------------------

def test_it_creates_a_branch_named_after_the_agent(repo):
    tree = wt.create(repo, "bob")

    assert tree.path == repo.parent / "proj-bob"
    assert tree.branch == "collab/bob"
    assert tree.created
    assert (tree.path / "README.md").exists(), "a real checkout, not an empty dir"
    assert (tree.path / ".git").exists()


def test_the_worktree_is_its_own_repo_root(repo):
    """Which is the whole point: collab keys its state on the git top level."""
    from collab.config import repo_root

    tree = wt.create(repo, "bob")
    assert repo_root(tree.path) == tree.path
    assert repo_root(tree.path) != repo


def test_running_it_twice_reattaches(repo):
    first = wt.create(repo, "bob")
    second = wt.create(repo, "bob")

    assert second.path == first.path
    assert not second.created, "reattached, not a second copy"


def test_a_name_with_awkward_characters_still_works(repo):
    tree = wt.create(repo, "bob/../etc")
    assert tree.path.parent == repo.parent
    assert ".." not in str(tree.path)
    assert tree.path.exists()


def test_two_agents_get_two_worktrees(repo):
    bob = wt.create(repo, "bob")
    carol = wt.create(repo, "carol")
    assert bob.path != carol.path
    assert {bob.branch, carol.branch} == {"collab/bob", "collab/carol"}


def test_a_chosen_path_is_honoured(repo, tmp_path):
    where = tmp_path / "elsewhere"
    tree = wt.create(repo, "bob", where)
    assert tree.path == where and where.exists()


def test_it_refuses_a_directory_that_is_not_ours(repo, tmp_path):
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "something").write_text("mine")

    with pytest.raises(RuntimeError, match="already exists"):
        wt.create(repo, "bob", occupied)


def test_a_repo_with_no_commits_says_so(tmp_path):
    bare = tmp_path / "fresh"
    bare.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=bare, check=True)

    with pytest.raises(RuntimeError, match="no commits"):
        wt.create(bare, "bob")


def test_a_plain_directory_says_so(tmp_path):
    plain = tmp_path / "notgit"
    plain.mkdir()
    with pytest.raises(RuntimeError, match="not a git repository"):
        wt.create(plain, "bob")


def test_removing_it(repo):
    tree = wt.create(repo, "bob")
    assert wt.remove(repo, tree.path, force=True)
    assert not tree.path.exists()
    assert not wt.existing(repo, tree.path)
