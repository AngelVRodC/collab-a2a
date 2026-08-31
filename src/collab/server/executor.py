"""Bridges A2A ``SendMessage`` into the collab hub.

A stock A2A client can drive the whole thing: it sends a Message whose
structured Part carries a collab envelope, we fan it out, and it gets an
acknowledgement Message back carrying the assigned ``seq``.
"""

from __future__ import annotations

import json

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events.event_queue_v2 import EventQueue
from a2a.types import Message, Role
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Value

from ..protocol import DEFAULT_ROOM, Envelope, KIND_CHAT, new_id
from .hub import Hub


def _envelope_from_message(msg: Message, sender: str) -> Envelope:
    """Read a collab envelope out of an A2A Message.

    A structured Part is the real path.  A plain-text Part is accepted too, so
    a bare A2A client with no knowledge of collab can still say something and
    have it land in the default room.
    """
    text_bits: list[str] = []
    for part in msg.parts:
        which = part.WhichOneof("content")
        if which == "data":
            payload = MessageToDict(part.data)
            if isinstance(payload, dict) and payload.get("collab"):
                env = Envelope.from_dict(payload)
                env.sender = sender
                env.seq = None  # only the hub assigns seq
                return env
        elif which == "text":
            text_bits.append(part.text)
    return Envelope(
        kind=KIND_CHAT,
        text="\n".join(text_bits).strip(),
        room=DEFAULT_ROOM,
        sender=sender,
    )


def ack_message(env: Envelope) -> Message:
    msg = Message(message_id=new_id("msg"), role=Role.ROLE_AGENT)
    part = msg.parts.add()
    value = Value()
    ParseDict({"collab": "v1", "kind": "ack", "seq": env.seq, "ts": env.ts}, value)
    part.data.CopyFrom(value)
    part.media_type = "application/json"
    return msg


class CollabAgentExecutor(AgentExecutor):
    def __init__(self, hub: Hub) -> None:
        self.hub = hub

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        sender = "anonymous"
        call_context = context.call_context
        if call_context is not None and call_context.user.is_authenticated:
            sender = call_context.user.user_name

        env = _envelope_from_message(context.message, sender)
        env = await self.hub.publish(env)
        await event_queue.enqueue_event(ack_message(env))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # Messages are delivered synchronously on publish, so by the time a
        # cancel could arrive there is nothing left to stop.
        return None
