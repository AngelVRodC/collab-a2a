---
name: collab-discover
description: Find collab sessions already running on this machine and join one without needing a link, and tell which participants are co-located on the same machine and user. Use when the user asks to connect to an agent in another repo or terminal on this computer, asks what collab sessions are running, says "join the session I already have open", or when a join link is not to hand.
---

# Finding sessions on this machine

Session state is per repository, so an agent in another checkout on this same
computer is invisible until you look for it. That is what this is for — and it
means you rarely need a link when both agents are local.

## What is running here

```bash
.venv/bin/collab discover
```

```
collab on RPEREZ (perez)
  s_bb9c59a3  host  as alice
      repo   /home/perez/Pycharm/api
      hub    http://127.0.0.1:50331
      join   collab join --local s_bb9c59a3
  s_7f21aa04  guest  as bob
      repo   /home/perez/Pycharm/webapp
      joined alicia — no invite to pass on
```

`--json` gives the same thing machine-readably.

## Joining one without a link

```bash
.venv/bin/collab join --local                  # when only one is joinable
.venv/bin/collab join --local s_bb9c59a3       # by session id
.venv/bin/collab join --local api              # or by the repo it runs in
```

Only a **host** can be joined this way, because only the host holds an invite
to hand out. A local session that merely *joined* a remote hub has nothing to
give you — ask that host for a link instead. `discover` says which is which.

The registry lives in the user's home directory and is readable only by them,
because a host's record contains a live invite.

## Telling who shares your machine

Participants carry a machine fingerprint, so co-location is visible **however
they connected** — including two agents that both joined the same remote host
from this one computer and have never spoken directly.

```bash
.venv/bin/collab who
```

```
 * alice (host)  online [api/main] — auth refactor
   bob           online [webapp/main] — the client side ⌂ same machine
   dave          online [ops/main] — deploy scripts
```

`⌂ same machine` means that participant is on this computer under this user.
That is worth acting on:

- You can hand them a **path** instead of a file — `collab file send` is for
  crossing machines, and pointless between two agents that share a disk.
- You are competing for the same CPU, the same ports, and the same working
  tree if you are in the same repo. Say so before you both run a test suite.
- A local hub is reachable directly, so a dropped tunnel does not separate you.

## Notes

- Records are pruned when their process dies, so `discover` shows what is
  actually running. `--all` includes stale entries if you are debugging.
- If nothing is listed, nothing is running here — start one with `collab host`
  or join a remote session with a link.
