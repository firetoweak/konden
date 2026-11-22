from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from konden.assistant.host import codex_chat
from konden.assistant.host.codex_app_server import CodexRequestError

IDENTITY = "1" * 32
THREAD_ID = str(UUID(IDENTITY))
TEXT = "请继续刚才的讨论。"


def test_direct_send_uses_native_user_input_and_returns_saved_history(tmp_path: Path):
    class Server:
        def __init__(self):
            self.writers = set()
            self.reader = SimpleNamespace(done=lambda: False)

        async def request(self, method, params):
            assert params["threadId"] == THREAD_ID
            if method == "thread/resume":
                assert params["approvalPolicy"] == "never" and params["sandbox"] == "workspace-write"
                return {"thread": {"id": THREAD_ID, "cwd": str(tmp_path)}}
            if method == "thread/unsubscribe":
                return {}
            assert method == "thread/turns/list"
            return {"nextCursor": None, "data": [{"status": "completed", "items": [
                {"type": "userMessage", "id": "user", "content": [{"type": "text", "text": TEXT}]},
                {"type": "functionCallOutput", "id": "external", "output": "tool content"},
                {"type": "agentMessage", "id": "note", "phase": "commentary", "text": "Thinking"},
                {"type": "agentMessage", "id": "reply", "phase": "final_answer", "text": "可以接着聊。"},
            ]}]}

        async def run_turn(self, thread_id, params, *, timeout):
            assert thread_id == THREAD_ID
            assert params == {"input": [{"type": "text", "text": TEXT}]}
            return {"id": "new-turn", "status": "completed"}

    link = SimpleNamespace(require=AsyncMock(return_value=Server()))
    result = asyncio.run(codex_chat.send_codex_chat(link, IDENTITY, TEXT))
    assert result["last_status"] == "completed"
    assert result["messages"] == [
        {"id": "user", "role": "user", "text": TEXT},
        {"id": "reply", "role": "codex", "text": "可以接着聊。"},
    ]


def test_busy_native_chat_does_not_submit_a_turn():
    class BusyServer:
        def __init__(self):
            self.writers = set()

        async def request(self, method, params):
            assert method == "thread/resume"
            raise CodexRequestError(method, {"code": -32600, "message": f"thread {params['threadId']} already has an active writer"})

    link = SimpleNamespace(require=AsyncMock(return_value=BusyServer()))
    with pytest.raises(codex_chat.CodexChatUnavailable, match="正在使用这段会话"):
        asyncio.run(codex_chat.send_codex_chat(link, IDENTITY, TEXT))
