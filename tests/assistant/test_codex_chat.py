from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest

from konden.assistant.host import codex_chat
from konden.assistant.host.codex_app_server import CodexRequestError

IDENTITY = "1" * 32
THREAD_ID = str(UUID(IDENTITY))
TEXT = "请继续刚才的讨论。"


def test_direct_send_uses_native_user_input_and_returns_saved_history(monkeypatch, tmp_path: Path):
    class Server:
        async def request(self, method, params):
            assert params["threadId"] == THREAD_ID
            if method == "thread/resume":
                assert params["approvalPolicy"] == "never" and params["sandbox"] == "workspace-write"
                return {"thread": {"id": THREAD_ID, "cwd": str(tmp_path)}}
            if method == "turn/start":
                assert params["input"] == [{"type": "text", "text": TEXT}]
                return {"turn": {"id": "new-turn"}}
            assert method == "thread/turns/list"
            return {"nextCursor": None, "data": [{"status": "completed", "items": [
                {"type": "userMessage", "id": "user", "content": [{"type": "text", "text": TEXT}]},
                {"type": "functionCallOutput", "id": "external", "output": "tool content"},
                {"type": "agentMessage", "id": "note", "phase": "commentary", "text": "Thinking"},
                {"type": "agentMessage", "id": "reply", "phase": "final_answer", "text": "可以接着聊。"},
            ]}]}

        async def wait_for_turn(self, thread_id, turn_id):
            assert (thread_id, turn_id) == (THREAD_ID, "new-turn")

    @asynccontextmanager
    async def open_server(_executable):
        yield Server()

    monkeypatch.setattr(codex_chat, "open_codex_app_server", open_server)
    result = asyncio.run(codex_chat.send_codex_chat(IDENTITY, TEXT))
    assert result["last_status"] == "completed"
    assert result["messages"] == [
        {"id": "user", "role": "user", "text": TEXT},
        {"id": "reply", "role": "codex", "text": "可以接着聊。"},
    ]


def test_busy_native_chat_does_not_submit_a_turn(monkeypatch):
    class BusyServer:
        async def request(self, method, params):
            assert method == "thread/resume"
            raise CodexRequestError(method, {"code": -32600, "message": f"thread {params['threadId']} already has an active writer"})

    @asynccontextmanager
    async def open_server(_executable):
        yield BusyServer()

    monkeypatch.setattr(codex_chat, "open_codex_app_server", open_server)
    with pytest.raises(codex_chat.CodexChatUnavailable, match="正在使用这段会话"):
        asyncio.run(codex_chat.send_codex_chat(IDENTITY, TEXT))
