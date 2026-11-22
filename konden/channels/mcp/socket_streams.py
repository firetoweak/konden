"""Carry the MCP SDK's message streams over a WebSocket."""

from contextlib import asynccontextmanager

from anyio import create_memory_object_stream, create_task_group
from mcp.shared.message import SessionMessage
from mcp.types import JSONRPCMessage
from pydantic import TypeAdapter, ValidationError

_MESSAGES = TypeAdapter(JSONRPCMessage)


@asynccontextmanager
async def socket_streams(receive, send, closed, *, metadata=None):
    incoming, reader = create_memory_object_stream[SessionMessage | Exception](0)
    writer, outgoing = create_memory_object_stream[SessionMessage](0)

    async def read_frames():
        async with incoming:
            while True:
                try:
                    frame = await receive()
                except closed:
                    return
                try:
                    message = _MESSAGES.validate_json(frame)
                except ValidationError as error:
                    await incoming.send(error)
                else:
                    await incoming.send(SessionMessage(message, metadata=metadata))

    async def write_frames():
        async with outgoing:
            async for message in outgoing:
                try:
                    await send(message.message.model_dump_json(by_alias=True, exclude_none=True))
                except closed:
                    return

    async with reader, writer, create_task_group() as group:
        group.start_soon(read_frames)
        group.start_soon(write_frames)
        try:
            yield reader, writer
        finally:
            group.cancel_scope.cancel()
