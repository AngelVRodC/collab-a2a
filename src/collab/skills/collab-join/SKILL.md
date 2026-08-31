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

`--name` is optional; without it collab uses the user's global name. Names must
be unique in a session, so pass one if the default is already taken.

If collab is not installed, follow `AGENT_INSTALL.md` first.

**If the join is refused** with *the name is already taken*, someone in the
session already answers to it. Names are unique so a direct message is never a
guess — pick another and tell the user which you used:

```bash
.venv/bin/collab join '<url>' --name <another>
```

**No link at all?** If the other agent is on this same machine you do not need
one — see the `collab-discover` skill, or just:

```bash
.venv/bin/collab discover
.venv/bin/collab join --local
```

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

## Leaving

```bash
.venv/bin/collab kill
```

As a guest this stops **your** listener. The hub belongs to the host and keeps
running for everyone else, so this is leaving, not ending the session.

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

## Reporting your own usage

Claude Code and Antigravity are picked up automatically. **Any other agent
reports for itself**, or it shows up on the roster with no figures and nobody
can weigh you when splitting work:

```bash
.venv/bin/collab stats --report '{"model":"<yours>","quota_five_hour":73}'
```

Better than remembering to repeat that: give collab a command that prints your
usage, and it will re-run it on a timer by itself.

```bash
.venv/bin/collab stats --source 'my-usage-script' --interval 120
```

Reports merge, so a partial one never erases the rest. Report nothing rather
than guessing — an invented quota gets someone handed work they cannot do.

All fields optional: `model`, `cost_usd`, `quota_used_pct`, `quota_five_hour`,
`quota_seven_day`, `context_pct`, `tokens_in`, `tokens_out`. Quota is percent
**used**. Send it again when the numbers move, not every turn.

## Dividing work on evidence, not guesswork

Every agent reports what it knows about its own usage — model, spend, quota,
context — and the whole session can read it:

```bash
.venv/bin/collab stats --json
```

Use it before handing out anything long. Read **all** the windows, and their
reset times — they lead to opposite decisions:

- 91% of a five-hour window that resets in 10 minutes → worth waiting.
- 88% of a monthly spend cap → give the work to somebody else.

Windows are listed busiest-first, so the one that will actually stop an agent
is the one you read first. That is the entire reason the figures are shared.

`⌂ same machine` in `collab who` means that agent is on this computer under this
user. You can pass it a path rather than a file, and you are competing for the
same CPU and ports.

## Showing the user what is happening

`collab watch --tmux` opens a full-screen view beside their work: the roster
with everyone's quota on top, the conversation below. See the `collab-watch`
skill.
