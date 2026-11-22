"""Read local Codex chat metadata without resuming chats or loading their history."""
from __future__ import annotations

import asyncio
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from konden.assistant.host.codex_app_server import (
    CodexDisconnected,
    CodexRequestError,
)


class ThreadSummary(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str
    name: str | None = None
    cwd: str | None = None


async def read_codex_threads(identities: list[str], *, server) -> dict[str, ThreadSummary]:
    if not identities or server is None:
        return {}
    try:
        async with asyncio.timeout(5):
            threads = {}
            for identity in dict.fromkeys(identities):
                thread_id = str(UUID(identity))
                try:
                    result = await server.request("thread/read", {"threadId": thread_id, "includeTurns": False})
                except CodexRequestError as error:
                    if error.error == {"code": -32600, "message": f"thread not loaded: {thread_id}"}:
                        continue
                    raise
                thread = ThreadSummary.model_validate(result["thread"])
                if thread.id != thread_id:
                    raise ValueError("Codex replied with a different thread id")
                threads[identity] = thread
            return threads
    except (CodexDisconnected, TimeoutError):
        # Optional local metadata may be unavailable; it never determines identity or delivery.
        return {}


async def read_codex_titles(identities: list[str], *, server) -> dict[str, str]:
    return {identity: thread.name for identity, thread in (await read_codex_threads(identities, server=server)).items()
            if thread.name is not None and thread.name.strip()}
