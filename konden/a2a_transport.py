"""HTTP client for a delegated A2A task."""

from __future__ import annotations

import asyncio

from a2a.client import A2AClientError, ClientConfig, create_client
from a2a.types import GetTaskRequest, SendMessageRequest, Task
from a2a.utils.errors import InternalError
import httpx


class ResultUnknown(RuntimeError):
    def __init__(self, task_id):
        self.task_id = task_id
        super().__init__("暂未确认这次 Codex 委派的结果。请先核对，避免重复派单。")


async def send_task(url: str, token: str, message) -> Task:
    task_id = None
    try:
        async with asyncio.timeout(600):
            async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                         timeout=httpx.Timeout(15, read=600), trust_env=False) as http:
                async with await create_client(url, ClientConfig(httpx_client=http)) as client:
                    async for event in client.send_message(SendMessageRequest(message=message)):
                        if event.HasField("task"):
                            task_id = event.task.id
                    if not task_id:
                        raise ResultUnknown(None)
                    task = await client.get_task(GetTaskRequest(id=task_id))
    except (A2AClientError, InternalError, httpx.TransportError, TimeoutError) as error:
        raise ResultUnknown(task_id) from error
    if task.id != task_id:
        raise ValueError("A2A returned a different task")
    return task
