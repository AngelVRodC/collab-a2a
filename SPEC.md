# The collab extension, v1

`collab` is an [A2A](https://a2a-protocol.org) agent with one extension. This
document specifies the extension; everything not described here is plain A2A.

- **Extension URI** — `https://github.com/collab-a2a/collab/ext/v1`
- **Declared at** — `AgentCard.capabilities.extensions[].uri`
- **Signalled by** — `A2A-Extensions: https://github.com/collab-a2a/collab/ext/v1`
- **Protocol version** — A2A `1.0`, with `0.3` accepted for compatibility

## 1. Why an extension is needed

A2A is point-to-point: a client sends, a server answers. An agent that wants to
*receive* must therefore be a reachable server — which fails the moment the
other person's agent is a laptop behind NAT.

`collab` inverts the arrangement. **The hub is the A2A agent**; every
participant is an A2A *client*. That solves reachability, but leaves three gaps
A2A does not cover, and this extension fills exactly those:

| Gap | Why core A2A cannot do it |
|---|---|
| Delivering a **third party's** message to you | `SendStreamingMessage` streams one request's events back to *its own caller* |
| A **durable, resumable** inbox | `SubscribeToTask` would mean modelling a mailbox as a Task that never terminates, and offers no gap-free resume |
| **Who else is here**, and who owns which task | A2A has no concept of a room, a roster, or a shared task |

## 2. The envelope

Every collab payload travels inside a standard A2A `Message` as a structured
(JSON) `Part`. A stock A2A client sees valid A2A; a collab-aware client sees:

```jsonc
{
  "collab": "v1",
  "kind":   "chat" | "task" | "file" | "hello" | "presence" | "system",
  "from":   "bob",                    // set by the hub from the bearer token
  "room":   "auth-refactor",          // omitted for direct messages
  "to":     "alice",                  // set for direct messages only
  "thread": "th_7f3a",                // optional
  "text":   "on it, starting now",
  "body":   { },                      // kind-specific, see below
  "seq":    412,                      // hub-assigned, monotonic per session
  "ts":     "2026-08-30T18:48:02Z"
}
```

`from` is **never** taken from the client. The hub sets it from the
authenticated participant, so a message cannot be attributed to someone else.

`seq` is assigned on append, is monotonic per session, and doubles as the SSE
`id:`. It is the only thing a client needs in order to resume losslessly.

### Envelope bodies by kind

| kind | `body` |
|---|---|
| `chat` | *(empty; the message is in `text`)* |
| `hello` | `{repo, branch, dirty, remote, cwd, focus}` |
| `presence` | `{event, was?}` |
| `task` | `{action, id, title, state, owner}` — `state` is a real A2A `TaskState` |
| `file` | `{action: "shared"\|"received", id, name, size, sha256, url}` |
| `system` | free-form |

## 3. Transport

### 3.1 The A2A surface (unmodified)

| Path | Purpose |
|---|---|
| `GET /.well-known/agent-card.json` | discovery; **no auth required** |
| `POST /a2a` | JSON-RPC 2.0 |
| `/rest/...` | HTTP+JSON binding |

JSON-RPC method names in A2A 1.0 are gRPC-style: `SendMessage`,
`SendStreamingMessage`, `GetTask`, `ListTasks`, `CancelTask`, `SubscribeToTask`,
`GetExtendedAgentCard`. **`A2A-Version: 1.0` must be sent**, or the request is
interpreted as 0.3.

The 0.3 spellings (`message/send`, `message/stream`, `tasks/get`,
`tasks/resubscribe`, …) are also accepted, since most clients in the wild still
speak them.

### 3.2 The extension surface

All of these require `Authorization: Bearer <participant token>` except `/join`.

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/ext/collab/v1/join` | invite + `hello` → token **+ session snapshot** |
| `GET` | `/ext/collab/v1/events` | **SSE feed**, honours `Last-Event-ID` |
| `POST` | `/ext/collab/v1/messages` | post an envelope (convenience; `SendMessage` does the same) |
| `GET` | `/ext/collab/v1/history` | backfill, `?room=&limit=` |
| `GET`/`POST` | `/ext/collab/v1/rooms` | list / create rooms |
| `GET` | `/ext/collab/v1/participants` | roster |
| `GET` | `/ext/collab/v1/snapshot` | roster + tasks + recent messages |
| `POST` | `/ext/collab/v1/rename` | change your display name |
| `GET`/`POST` | `/ext/collab/v1/tasks` | the shared task board |
| `POST` | `/ext/collab/v1/files` | upload (multipart, ≤10 MB) |
| `GET` | `/ext/collab/v1/files/{id}/content` | download |
| `POST` | `/ext/collab/v1/files/{id}/ack` | confirm receipt → **deletes the file** |
| `DELETE` | `/ext/collab/v1/files/{id}` | withdraw (sender or host) |
| `POST` | `/ext/collab/v1/revoke` | remove a participant (**host only**) |
| `GET` | `/ext/collab/v1/health` | liveness; no auth |

## 4. The join handshake

`POST /ext/collab/v1/join`

```jsonc
{ "invite": "<code>", "name": "bob",
  "hello": {"repo": "collab", "branch": "main", "focus": "the client side"} }
```

The response carries three things at once, which is what makes joining and
collaborating a single step:

```jsonc
{ "token": "<per-participant bearer token>",
  "name":  "bob",              // may be suffixed (bob-2) if the name was taken
  "host":  "alice",
  "snapshot": {
    "participants": [{"name","is_host","connected","focus","repo","branch"}],
    "tasks":  [ ... open tasks ... ],
    "recent": [ ... last N envelopes ... ],
    "rooms":  ["general"], "seq": 12 } }
```

The hub then **broadcasts the `hello`** to everyone already present, so an
arriving agent shows up in their feed with its repo, branch and stated focus —
they can answer without being told to go and look.

## 5. The live feed

`GET /ext/collab/v1/events` → `text/event-stream`

```
id: 412
event: collab
data: {"collab":"v1","kind":"chat","from":"alice","text":"...","seq":412}
```

Events: `ready` (on connect), `collab` (an envelope), `keepalive` (every 15 s),
`closed` (you were removed).

**Resuming.** Send `Last-Event-ID: <seq>` (or `?since=`). The hub replays every
event after that seq before resuming live delivery. Sending `0` backfills the
whole session — a first connection therefore does not start blind.

**Delivery rules.** A room message goes to every subscriber. A direct message
goes only to its sender and recipient — *including on replay*. The sender
receives their own messages back, which is what keeps every participant's local
log identical and makes seq-based resume sound.

## 6. Authentication

| | |
|---|---|
| Scheme | `http` / `bearer`, declared in `AgentCard.securitySchemes` |
| Invite | `secrets.token_urlsafe(32)`, TTL 24 h, optional max-uses |
| Token | `secrets.token_urlsafe(32)`, one per participant, revocable |
| Storage | SHA-256 hashes only; compared with `secrets.compare_digest` |
| Failure | `401` with `WWW-Authenticate: Bearer realm="collab"` |
| Rate limit | `/join` — 10 attempts per minute per IP |

The invite travels in the **URL fragment** (`https://host#CODE`), so it is never
sent in a request line and stays out of proxy and server logs.

## 7. Shared tasks

Task states are the real A2A `TaskState` enum, so `tasks/get` and `tasks/list`
work unmodified.

```
propose  → TASK_STATE_SUBMITTED    (unclaimed)
claim    → TASK_STATE_WORKING      (owner set; a second claim gets 409)
update   → TASK_STATE_WORKING
complete → TASK_STATE_COMPLETED
fail     → TASK_STATE_FAILED
cancel   → TASK_STATE_CANCELED
```

Claiming is the mechanism that stops two agents starting the same work: the
second claim is refused with `409` naming the current owner.

## 8. File transfer

Artifacts and binaries move as files, not as pasted text.

1. `POST /ext/collab/v1/files` (multipart, **≤10 MB**, enforced while streaming
   so an oversized upload is never fully written). Returns id, sha256 and a
   download URL, and broadcasts a `file` envelope.
2. The recipient downloads from `/files/{id}/content`; the server's checksum is
   echoed in `X-Collab-Sha256`.
3. The recipient verifies the checksum and calls `/files/{id}/ack`, which
   **deletes the host's copy** and tells the sender it landed.

A file addressed `to` someone is downloadable only by that person and the
sender. Un-acked files are swept after 24 hours.
