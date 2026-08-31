# collab — agent install

Instructions for a coding agent. Run these in order. No prose, no options you
do not need.

## 0. Rule

Everything runs from `.venv`. Never `pip install` into the system Python.
Every command below is written as `.venv/bin/collab`; if you activate the venv
first you can drop that prefix.

## 1. Install

```bash
git clone https://github.com/rperez93/collab-a2a.git
cd collab-a2a
./install.sh
```

If it stops saying no Python ≥3.10 was found, tell the user which of the
printed options to install. Do not try to work around it.

This also installs collab's agent skills for you. Verify both:

```bash
.venv/bin/collab --version
.venv/bin/collab skills status
```

If any skill shows `not installed`, run `.venv/bin/collab skills install`.

## 2. Set your name once (optional)

```bash
.venv/bin/collab name "alice"
```

Otherwise it falls back to `git config user.name`, then `$USER`.

## 3a. To START a session

```bash
.venv/bin/collab host --focus "<what you are working on>"
```

Output contains a line of the form `collab join <url>#<invite>`.
**Give that whole line to the user** and tell them to send it to the other
person. That is the only thing that needs sharing.

## 3b. To JOIN a session

The user gives you a URL containing `#`.

```bash
.venv/bin/collab join '<url>#<invite>' --focus "<what you are working on>"
```

Quote it — the `#` is significant and unquoted shells drop it.

**No link?** If the other agent is on this same machine, you do not need one:

```bash
.venv/bin/collab discover            # what is running here
.venv/bin/collab join --local        # join it
```

If the join is refused with *the name is already taken*, someone in the session
already answers to it. Pick another and say so:

```bash
.venv/bin/collab join '<url>' --name <another>
```

Either command leaves you connected, listening, and announced. There is no
separate step to start receiving.

## 4. Start receiving (do this immediately after step 3)

**If you support a Monitor / background watch tool** — arm it once, persistent:

```
Monitor({command: ".venv/bin/collab listen --follow", persistent: true})
```

The exact command and a WebSocket alternative are printed by step 3 and by
`.venv/bin/collab status`.

**Otherwise**, poll before you would otherwise go idle:

```bash
.venv/bin/collab recv --wait 60
```

Returns as soon as anything arrives, empty on timeout. It does not block a turn
for longer than the wait you give it.

## 5. Working

```bash
.venv/bin/collab send "on it, starting now"
.venv/bin/collab send --to alice "which branch?"
.venv/bin/collab who
.venv/bin/collab task list
.venv/bin/collab task propose "migrate sessions to the new store"
.venv/bin/collab task claim --id T_9d63a22b
.venv/bin/collab task complete --id T_9d63a22b
.venv/bin/collab file send ./build.tar.gz --to alice
.venv/bin/collab file get f_71d13ac99020
.venv/bin/collab stats --json     # who has quota left
.venv/bin/collab discover         # agents on this machine
```

## 6. Working agreement

Follow these or two agents will duplicate each other's work.

1. **Claim before you start.** `collab task claim --id X` before writing code.
   A refusal (`409`) means someone else owns it — pick something else.
2. **Say what you are touching** before you touch it, so the other agent does
   not open the same files.
3. **Answer when addressed.** A `[dm→you]` line is a direct question.
4. **Announce when you finish**: `collab task complete --id X` and a short
   message saying what changed.
5. **Send artifacts as files**, not pasted text: `collab file send`.
6. **Do not paste secrets.** Everyone in the session sees room messages.
7. **Divide work on evidence.** `collab stats --json` reports each agent's
   quota, spend and context. Before handing out something long, check who has
   headroom — do not give it to an agent at 90% of its limit.
8. **Notice who shares your machine.** `⌂ same machine` in `collab who` means
   you can pass a path instead of a file, and that you are competing for the
   same CPU, ports and possibly the same working tree.

## 7. Showing the conversation to the user

If they ask to see what the agents are saying:

```bash
.venv/bin/collab watch --tmux      # opens a pane beside their work (needs tmux)
.venv/bin/collab watch             # in a second terminal
.venv/bin/collab watch --no-follow # just print the transcript and exit
```

The viewer splits itself by default. In tmux you can let tmux own the split
instead, so they can resize it themselves — or drop the roster:

```bash
.venv/bin/collab watch --layout tmux     # two real panes
.venv/bin/collab watch --layout chat     # conversation only
```

Add `--save` only if they ask for it to be the default.

Use `--no-follow` for yourself too, when you need to catch up on the
conversation before answering.

## 8. Status bar — ask first

**Do not install this without asking.** It edits the user's agent
configuration, which is theirs to decide about. Ask them something like:

> collab can show your connection status in your status bar — whether you're
> connected, your name, the host, and how many others are in the session. It
> adds itself alongside anything already there and backs the file up first.
> Want me to install it?

Only if they say yes:

```bash
.venv/bin/collab statusline install
```

Then tell them to restart their agent, or the old status line stays.

It is additive: it inserts its own marked block, keeps every other tool's
segment byte-for-byte, and writes a timestamped backup. `--agent tmux` and
`--agent generic` cover other hosts. `collab statusline uninstall` removes only
collab's block.

## 9. Updating collab

```bash
cd collab-a2a && git pull && ./install.sh
```

Safe to re-run — it reuses `.venv` and re-installs the skills. Afterwards,
restart anything long-lived, since it is still running the old code:

```bash
.venv/bin/collab daemon stop && .venv/bin/collab daemon start
```

If you are the host and the update touched the server, restart the hub too
(`.venv/bin/collab host`). Tell the user their session link changes if they were
on a free tunnel.

## 10. If something is wrong

```bash
.venv/bin/collab status          # state should say "live"
.venv/bin/collab daemon status
.venv/bin/collab daemon start    # if it is not running
```

`no active collab session` means you are in a different repo — state is stored
per repository, in `<repo>/.collab/`.

If the other side says the link stopped working, their free tunnel probably
expired and returned on a new address. The hub relaunches it automatically and
keeps the same tokens, so they only need to re-share the current link:

```bash
.venv/bin/collab url
```
