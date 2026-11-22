"""Direct human messages and history for locally created Codex conversations."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from konden.assistant.external_messages import external_name
from konden.assistant.host.codex_app_server import (
    CodexDisconnected,
    CodexRequestError,
    open_codex_app_server,
)
from konden.assistant.host.codex_continuation import default_codex_executable
from konden.assistant.host.codex_titles import ThreadSummary

_GUI_PERMISSIONS = {"approvalPolicy": "never", "sandbox": "workspace-write"}


class CodexChatUnavailable(ValueError):
    pass


@asynccontextmanager
async def _chat_server():
    try:
        async with open_codex_app_server(default_codex_executable()) as server:
            yield server
    except FileNotFoundError as error:
        raise CodexChatUnavailable("找不到本机 Codex。请安装并登录 Codex 后重试。") from error
    except (CodexDisconnected, BrokenPipeError, ConnectionResetError, TimeoutError) as error:
        raise CodexChatUnavailable("未收到 Codex 的确认。请刷新会话或在 Desktop 查看后再决定是否重发。") from error


def _thread(result, expected_id=None):
    summary = ThreadSummary.model_validate(result["thread"])
    if expected_id is not None and summary.id != expected_id:
        raise ValueError("Codex replied with a different thread id")
    return summary


async def create_codex_chat(workspace: Path) -> str:
    async with _chat_server() as server:
        async with asyncio.timeout(15):
            result = await server.request("thread/start", {"cwd": str(workspace), **_GUI_PERMISSIONS})
            thread_id = _thread(result).id
            identity = UUID(thread_id).hex
            await server.request("thread/name/set", {"threadId": thread_id, "name": external_name(identity)})
            return identity


def _message(item):
    if item["type"] == "userMessage":
        text = "\n".join(block["text"] for block in item["content"] if block["type"] == "text")
        role = "user"
    elif item["type"] == "agentMessage" and item.get("phase") in (None, "final_answer"):
        text, role = item["text"], "codex"
    else:
        return None
    return {"id": item["id"], "role": role, "text": text} if text else None


async def _history(server, thread):
    result = await server.request("thread/turns/list", {
        "threadId": thread.id, "limit": 20, "sortDirection": "desc", "itemsView": "full",
    })
    turns = result["data"]
    messages = [
        message for turn in reversed(turns) for item in turn["items"]
        if (message := _message(item)) is not None
    ]
    if thread.cwd is None:
        raise ValueError("Codex did not report its working directory")
    return {
        "thread_id": thread.id, "workspace": thread.cwd, "messages": messages,
        "last_status": turns[0]["status"] if turns else None, "has_earlier": result["nextCursor"] is not None,
    }


async def read_codex_chat(identity: str):
    thread_id = str(UUID(identity))
    async with _chat_server() as server:
        async with asyncio.timeout(15):
            result = await server.request("thread/read", {"threadId": thread_id, "includeTurns": False})
            return await _history(server, _thread(result, thread_id))


async def send_codex_chat(identity: str, text: str):
    thread_id = str(UUID(identity))
    async with _chat_server() as server:
        async with asyncio.timeout(15):
            try:
                resumed = await server.request("thread/resume", {
                    "threadId": thread_id, "excludeTurns": True, **_GUI_PERMISSIONS,
                })
            except CodexRequestError as error:
                if error.error == {"code": -32600, "message": f"thread {thread_id} already has an active writer"}:
                    raise CodexChatUnavailable("Codex Desktop 或另一个窗口正在使用这段会话。可在当前窗口继续。") from error
                raise
            thread = _thread(resumed, thread_id)
            started = await server.request("turn/start", {
                "threadId": thread_id, "input": [{"type": "text", "text": text}],
            })
        async with asyncio.timeout(900):
            await server.wait_for_turn(thread_id, started["turn"]["id"])
        async with asyncio.timeout(15):
            return await _history(server, thread)
