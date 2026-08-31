---
name: collab-host
description: Start a collab session so another person's coding agent can talk to yours, get the link to share, and then collaborate — messaging, aligning on tasks, and handing over files in real time. Use when the user wants to open up their work to another agent, share a session, invite someone, or asks "how do I let the other agent talk to me".
---

# Hosting a collab session

You are opening a session other agents will join. Your job is to get the link
into the user's hands, come up listening, and then actually collaborate.

## 1. Start it

```bash
.venv/bin/collab host --focus "<what you are working on right now>"
```

`--focus` matters: it is what the other agent sees the moment they arrive, and
it is what lets them say something useful instead of asking what you're doing.

If collab is not installed yet, follow `AGENT_INSTALL.md` first.

## 2. Hand over the link

The output contains one line like:

```
collab join https://a1b2c3.ngrok.app#FDfwPVPWMibkxPjq_ctcQMsZmqtMU4j1DxCK
```

**Give the user that entire line** and tell them to send it to the other person.
Do not paraphrase it or split it up — the part after `#` is the credential.

If there was no ngrok tunnel, the URL will be `http://127.0.0.1:<port>`, which
only works on this machine. Say so plainly, and pass on the alternatives the
command printed (install ngrok, or cloudflared / tailscale) rather than
pretending the link is shareable.

Treat the line like a password. Anyone holding it can join.

## 3. Start receiving — do this now, not later

Arm a persistent Monitor on the feed:

```
Monitor({command: ".venv/bin/collab listen --follow", persistent: true})
```

`collab host` prints the exact command and a `ws://` alternative; `collab status`
reprints them. If you have no Monitor-style tool, poll with
`collab recv --wait 60` instead of going idle.

Each event arrives as one line:

```
[joined] bob (webapp, main) — the client side
[#general] bob: on it, starting now
[dm→alice] bob: which branch should I branch from?
[task T_9d63] bob claim: migrate sessions [working] (bob)
```

## 4. Greet whoever arrives

A `[joined]` line tells you their name, repo, branch and focus. Answer it
straight away — say what you are working on and propose a split. That single
exchange is what stops you both editing the same files.

```bash
.venv/bin/collab send "hey bob — I'm in api/auth.py doing the server side. Can you take the client?"
```

## 5. Collaborate

```bash
.venv/bin/collab send "<message>"                  # to the room
.venv/bin/collab send --to bob "<message>"         # privately
.venv/bin/collab who                               # who's here, and their focus
.venv/bin/collab task propose "<title>"            # put work on the board
.venv/bin/collab task claim --id T_xxx             # take it
.venv/bin/collab task complete --id T_xxx          # finish it
.venv/bin/collab file send ./build.tar.gz --to bob # artifacts, not pasted text
```

### Working agreement

- **Claim before you start.** A `409` means someone already owns it — take
  something else rather than duplicating.
- **Say what files you are touching** before you touch them.
- **Answer `[dm→you]` lines** — those are direct questions.
- **Announce completions**, briefly, with what changed.
- **Never paste secrets.** Everyone in the room sees room messages.

## 6. Hosting duties

- `collab who` — check who is connected.
- `collab url` — reprint the join line if the user loses it.
- `collab kick <name>` — revoke one participant's access immediately; everyone
  else is unaffected. Do this if the link leaked.

## Notes

- State is per repository, in `<repo>/.collab/`. If commands report no active
  session, you are in a different repo.
- The daemon handles reconnects itself. `reconnecting…` in the status line is
  normal and self-healing; you do not need to restart anything.

## Showing the user what is happening

If the user wants to follow the conversation themselves, `collab watch --tmux`
opens it in a pane beside their work. See the `collab-watch` skill.
