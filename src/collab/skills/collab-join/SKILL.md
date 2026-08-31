---
name: collab-join
description: Join another agent's collab session from a shared URL and start collaborating immediately — receiving their messages in real time, aligning on who does what, and exchanging files. Use when the user pastes a collab join link or URL containing '#', or asks to connect to someone else's agent, join a session, or work with another person's coding agent.
---

# Joining a collab session

The user has a link from someone else. Get connected, announce yourself, and
start working with the other agent — in one pass.

## 1. Join

```bash
.venv/bin/collab join '<url>#<invite>' --focus "<what you are working on>"
```

**Quote the URL.** The `#` is part of the credential and an unquoted shell will
throw away everything after it.

`--focus` is what the other agent sees when you arrive, so make it specific:
`"the client side of the auth refactor"`, not `"coding"`.

If collab is not installed, follow `AGENT_INSTALL.md` first.

That single command joins, announces you, starts the listener, and prints the
session snapshot. There is no separate step to start receiving.

## 2. Read the snapshot you just got

The output tells you who is present, their repo, branch and focus, the open
tasks with owners, and the recent conversation:

```
Who's here
   alice (host)  online [collab/main] — auth refactor
 * bob           online [webapp/main] — the client side

Open tasks
  T_9d63a22b  migrate sessions to the new store  [submitted]  unclaimed
```

Use it. You now know what they are doing and what is unclaimed, so your first
message can be substantive.

## 3. Start receiving

Arm a persistent Monitor:

```
Monitor({command: ".venv/bin/collab listen --follow", persistent: true})
```

The join output prints the exact command plus a `ws://` alternative; so does
`collab status`. Without a Monitor-style tool, poll with
`collab recv --wait 60` rather than going idle.

## 4. Say something useful immediately

Do not wait to be spoken to. Reference what the snapshot told you:

```bash
.venv/bin/collab send "hi alice — I see you're on the auth refactor. I'll take the client side. Shall I claim T_9d63?"
```

## 5. Collaborate

```bash
.venv/bin/collab send "<message>"                    # to the room
.venv/bin/collab send --to alice "<message>"         # privately
.venv/bin/collab who                                 # roster and focus
.venv/bin/collab task list                           # the board
.venv/bin/collab task claim --id T_xxx               # take work
.venv/bin/collab task complete --id T_xxx            # finish it
.venv/bin/collab file send ./patch.diff --to alice   # artifacts, not pasted text
.venv/bin/collab file get f_xxx                      # fetch what they sent
```

### Working agreement

- **Claim before you start.** `collab task claim --id X` first. A `409` means
  someone owns it already — pick something else.
- **Say what files you are touching**, so you do not both edit the same ones.
- **Answer `[dm→you]` lines** — they are direct questions to you.
- **Announce completions** with a short note on what changed.
- **Send artifacts as files.** `collab file send` — do not paste binaries or
  long diffs into messages. Fetching verifies the checksum and then deletes the
  host's copy automatically.
- **Never paste secrets.** Room messages are visible to everyone present.

## 6. If it goes quiet

```bash
.venv/bin/collab status         # "state" should be live
.venv/bin/collab daemon start   # if the daemon is not running
```

- `reconnecting…` is normal and self-healing — the daemon retries with backoff
  and replays anything missed. Do not restart it.
- `the hub rejected this token` means you were removed, or the host recreated
  the session. Ask the user for a fresh link.
- `no active collab session` means you are in a different repo — state lives in
  `<repo>/.collab/`.

## Showing the user what is happening

If the user wants to follow the conversation themselves, `collab watch --tmux`
opens it in a pane beside their work. See the `collab-watch` skill.
