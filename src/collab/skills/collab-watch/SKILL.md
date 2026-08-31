---
name: collab-watch
description: Show the human a live, readable transcript of the collab conversation between the agents — optionally in its own tmux pane so they can watch it alongside their work. Use when the user asks to see the conversation, follow along, watch what the agents are saying, open a panel or split for collab, or asks "what did the other agent say".
---

# Showing the conversation to the user

`collab listen` is built for agents — one terse line per event, so a Monitor can
turn each into a notification. It is not what you give a person.

`collab watch` is the human view: the transcript so far, colourised per speaker,
then live as it grows.

## If the user is in tmux — give them a pane

Check `$TMUX`. If it is set, this puts the conversation beside their work:

```bash
.venv/bin/collab watch --tmux
```

The pane opens to the right at 35% and starts following immediately. Options:

```bash
.venv/bin/collab watch --tmux --vertical      # split below instead
.venv/bin/collab watch --tmux --percent 50    # give it half the window
```

You stay in the original pane — the split runs detached, so your own session is
not interrupted.

## Letting tmux own the layout

By default the viewer splits itself: roster on top, conversation below. In tmux
you can hand that job to tmux instead, so the user resizes and rearranges with
the keys they already know — or drops the roster entirely.

```bash
.venv/bin/collab watch --layout tmux     # roster and chat as two real panes
.venv/bin/collab watch --layout chat     # conversation only
.venv/bin/collab watch --layout roster   # roster only
```

Where the roster goes, and how much room it gets:

```bash
.venv/bin/collab watch --layout tmux --roster-position left --roster-size 40
```

If the user says they want this every time, add `--save` and it becomes their
default — a bare `collab watch` then uses it:

```bash
.venv/bin/collab watch --layout tmux --roster-position left --save
```

Do not guess at this. `--layout tmux` needs tmux and falls back to the built-in
split without it, so it is safe to offer, but only set a saved default when the
user actually asks for one.

## If they are not in tmux

Do **not** try to start tmux for them and take over their terminal. Tell them to
run this in a second terminal:

```bash
.venv/bin/collab watch
```

Or, if they would like tmux to manage it:

```bash
tmux new-session -s collab '.venv/bin/collab watch'
```

## Just showing them the history inline

When they want to read what has happened rather than watch it, print it and
exit rather than leaving a follower running:

```bash
.venv/bin/collab watch --no-follow --limit 50
```

That is also the right form when *you* need to catch up on the conversation
before answering.

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

## Notes

- It reads the local inbox the daemon maintains, so it works even while the hub
  is briefly unreachable, and it fills in whatever was missed once the daemon
  reconnects.
- There is no session to break: it only reads. Closing the pane stops nothing.
- If it says there is no active session, the user is in a different repo —
  collab keeps state per repository in `<repo>/.collab/`.
