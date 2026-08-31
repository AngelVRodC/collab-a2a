"""The per-participant SSE feed.

This is the piece plain A2A does not provide: ``SendStreamingMessage`` streams
one request's events back to its own caller, so it cannot carry a third party's
message to you.  Here each participant holds one long-lived response fed by its
own queue, framed with ``id: <seq>`` so a reconnect can resume exactly.
"""

from __future__ import annotations

import asyncio
import json
import logging

from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request

from ..protocol import Envelope
from .hub import Hub

logger = logging.getLogger(__name__)

KEEPALIVE_SECONDS = 15.0


async def event_stream(request: Request, hub: Hub, participant: str) -> EventSourceResponse:
    """Open the feed for ``participant``, replaying anything they missed first."""
    last_event_id = request.headers.get("last-event-id") or request.query_params.get("since")
    try:
        resume_from = int(last_event_id) if last_event_id else None
    except ValueError:
        resume_from = None

    sub = await hub.subscribe(participant)

    async def generator():
        try:
            # Replay before live delivery. Anything that lands mid-replay is
            # already sitting in the queue, so the client sees each seq once.
            if resume_from is not None:
                missed = await asyncio.to_thread(
                    hub.store.since, resume_from, viewer=participant
                )
                for env in missed:
                    yield _frame(env)

            yield {
                "event": "ready",
                "data": json.dumps({
                    "participant": participant,
                    "resumed_from": resume_from,
                    "seq": hub.store.max_seq(),
                }),
            }

            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(sub.queue.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    # A comment frame; proves the connection is alive rather
                    # than merely quiet, so a dead link is detectable.
                    yield {"event": "keepalive", "data": "{}"}
                    continue
                if item is None:  # revoked
                    yield {"event": "closed", "data": json.dumps({"reason": "revoked"})}
                    break
                yield _frame(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("event stream failed for %s", participant)
        finally:
            await hub.unsubscribe(sub)

    return EventSourceResponse(generator())


def _frame(env: Envelope) -> dict[str, str]:
    return {
        "id": str(env.seq),
        "event": "collab",
        "data": json.dumps(env.to_dict()),
    }
