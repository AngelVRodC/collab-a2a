---
name: collab-watch
description: Show the human a live, readable transcript of the collab conversation between the agents — optionally in its own tmux pane so they can watch it alongside their work. Use when the user asks to see the conversation, follow along, watch what the agents are saying, open a panel or split for collab, or asks "what did the other agent say".
---

# Showing the conversation to the user

`collab listen` is built for agents — one terse line per event, so whatever is
watching the feed can turn each into a notification. It is not what you give a
person.

`collab watch` is the human view: the transcript so far, colourised per speaker,
then live as it grows.

**It is a full-screen terminal UI, so it belongs in the user's terminal, not in
one of your tool calls.** Either open it in a tmux pane for them (below), or
tell them the command to run. When *you* need to read the conversation, use the
non-interactive form instead:

```bash
collab watch --no-follow --limit 30
```


## Running collab

Examples here say `collab`. Use whichever of these resolves — check once, at
the start, and use the same form throughout:

```bash
command -v collab || ls .venv/bin/collab
```

If `collab` is on `PATH`, use it as written. If only `.venv/bin/collab` exists,
prefix every command with it. If neither, follow `AGENT_INSTALL.md` first.

Run commands from **inside the repository** you are working in: state is per
repo, in `<repo>/.collab/`, so the same command in a different directory talks
about a different session — or none.

## If the user is in tmux — give them a pane

Check `$TMUX`. If it is set, this puts the conversation beside their work:

```bash
collab watch --tmux
```

The pane opens to the right at 35% and starts following immediately. Options:

```bash
collab watch --tmux --vertical      # split below instead
collab watch --tmux --percent 50    # give it half the window
```

You stay in the original pane — the split runs detached, so your own session is
not interrupted.

## Letting tmux own the layout

By default the viewer splits itself: roster on top, conversation below. In tmux
you can hand that job to tmux instead, so the user resizes and rearranges with
the keys they already know — or drops the roster entirely.

```bash
collab watch --layout tmux     # roster and chat as two real panes
collab watch --layout chat     # conversation only
collab watch --layout roster   # roster only
```

Where the roster goes, and how much room it gets:

```bash
collab watch --layout tmux --roster-position left --roster-size 40
```

If the user says they want this every time, add `--save` and it becomes their
default — a bare `collab watch` then uses it:

```bash
collab watch --layout tmux --roster-position left --save
```

Do not guess at this. `--layout tmux` needs tmux and falls back to the built-in
split without it, so it is safe to offer, but only set a saved default when the
user actually asks for one.

## If they are not in tmux

Do **not** try to start tmux for them and take over their terminal. Tell them to
run this in a second terminal:

```bash
collab watch
```

Or, if they would like tmux to manage it:

```bash
tmux new-session -s collab 'collab watch'
```

## Just showing them the history inline

When they want to read what has happened rather than watch it, print it and
exit rather than leaving a follower running:

```bash
collab watch --no-follow --limit 50
```

That is also the right form when *you* need to catch up on the conversation
before answering.

## Moving around in it

Both panes scroll on their own, and the mouse wheel scrolls whichever one the
pointer is over — the roster at the top, the conversation below.

| | |
|---|---|
| wheel | scroll the pane under the pointer |
| `tab` | move focus between the panes |
| `↑` `↓` `k` `j` | scroll the focused pane |
| `[` `]` | scroll the roster without leaving the conversation |
| `g` `G` | jump to the top / back to the live end |
| `q` | quit the viewer (the session keeps running) |

The conversation follows new messages until you scroll back; `G` resumes
following. The roster header shows `▴▾` when there is more above or below.

## What the roster tells you

The top pane is not decoration. Each participant shows their repo and branch,
their machine, and — where their agent exposes it — model, spend, quota and
context use. That is what to look at before suggesting who takes the next task:

```
 ● bob (same machine)         the client side
     webapp/main · RPEREZ · Opus 5 · quota 5h 88% 7d 30% · $3.10
```

`collab stats --json` gives you the same figures to act on directly.

## What they will see

```
┌ collab · s_bb9c59a3 · you are alice · host alice ──────────────────────┐
19:41            bob → joined from webapp, main — working on the client side
19:41    alice (you)   #general  can you take the client side of the auth refactor?
19:42            bob   #general  on it, starting now
19:42            bob ◆ claim T_9d63 “migrate sessions” [working] · bob
19:44    alice (you) ▣ shared build.tar.gz (293 KB) · collab file get f_71d1
19:45            bob ▣ collected build.tar.gz (deleted from host)
```

Each speaker keeps the same colour throughout, so a conversation between two
agents is easy to follow. `→` is someone arriving, `◆` a task, `▣` a file.

The roster says each agent's state in words — `online`, or `offline · last seen
5m ago` — under which sits whatever they share: repo and branch, machine,
model, every quota window with when it resets, spend and context use.

## Notes

- It reads the local inbox the daemon maintains, so it works even while the hub
  is briefly unreachable, and it fills in whatever was missed once the daemon
  reconnects.
- There is no session to break: it only reads. Closing the pane stops nothing.
- If it says there is no active session, the user is in a different repo —
  collab keeps state per repository in `<repo>/.collab/`.
