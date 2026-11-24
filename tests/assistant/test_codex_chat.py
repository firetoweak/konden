from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from konden.assistant.host import codex_chat
from konden.assistant.host.codex_app_server import CodexRequestError

IDENTITY = "1" * 32
THREAD_ID = str(UUID(IDENTITY))
TEXT = "请继续刚才的讨论。"


def test_chat_catalog_keeps_first_entry_for_each_native_identity():
    first = {"id": THREAD_ID, "name": "同名会话", "cwd": "example"}
    other = {**first, "id": str(UUID(int=2))}
    duplicate = {**first, "id": IDENTITY, "cwd": "older"}
    request = AsyncMock(side_effect=[
        {"data": [first, other, duplicate]},
        CodexRequestError("project/list", {"code": -32601, "message": "Method not found"}),
    ])
    link = SimpleNamespace(require=AsyncMock(return_value=SimpleNamespace(request=request)))

    catalog = asyncio.run(codex_chat.list_codex_chats(link, ""))
    assert catalog["projects_available"] is False
    assert catalog["groups"] == [{"project": None, "chats": [
        {"identity": IDENTITY, "name": "同名会话", "workspace": "example"},
        {"identity": UUID(int=2).hex, "name": "同名会话", "workspace": "example"},
    ]}]


def test_chat_catalog_honors_registered_roots_and_groups_unique_worktrees(tmp_path):
    def repository(name, *worktrees):
        directory = tmp_path / name
        directory.mkdir()
        subprocess.run(["git", "init", "-q", str(directory)], check=True)
        subprocess.run([
            "git", "-C", str(directory), "-c", "user.name=verification", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "initial",
        ], check=True)
        for worktree in worktrees:
            subprocess.run(["git", "-C", str(directory), "worktree", "add", "-q", "--detach", str(worktree)], check=True)
        return directory

    personal = tmp_path / "personal"
    shared = tmp_path / "shared"
    main = repository("main", personal, shared)
    linked = tmp_path / "konden-worktree"
    konden = repository("konden", linked)
    projects = [
        {"id": "main", "name": "主项目", "roots": [{"path": str(main)}]},
        {"id": "personal", "name": "个人工作区", "roots": [{"path": str(personal)}]},
        {"id": "konden", "name": "konden", "roots": [{"path": str(konden)}]},
    ]
    threads = [
        {"id": str(UUID(int=number)), "name": title, "cwd": str(directory), **assignment}
        for number, (title, directory, assignment) in enumerate([
            ("独立登记", personal, {}),
            ("原生归属优先", main, {"projectId": "personal"}),
            ("相关 worktree", linked / "docs", {}),
            ("共用仓库归属不明确", shared, {}),
        ], 1)
    ]
    request = AsyncMock(side_effect=[{"data": threads}, {"data": projects, "nextCursor": None}])
    link = SimpleNamespace(require=AsyncMock(return_value=SimpleNamespace(request=request)))

    catalog = asyncio.run(codex_chat.list_codex_chats(link, ""))
    assert {
        group["project"]["id"] if group["project"] is not None else None: [chat["name"] for chat in group["chats"]]
        for group in catalog["groups"]
    } == {
        "main": [],
        "personal": ["独立登记", "原生归属优先"],
        "konden": ["相关 worktree"],
        None: ["共用仓库归属不明确"],
    }


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
                {"type": "functionCallOutput", "id": "work", "name": "a2a_delegation", "output": "请核对文稿。"},
                {"type": "agentMessage", "id": "note", "phase": "commentary", "text": "Thinking"},
                {"type": "agentMessage", "id": "reply", "phase": "final_answer", "text": "可以接着聊。"},
            ]}]}

        async def run_turn(self, thread_id, params, *, timeout, on_accepted):
            assert thread_id == THREAD_ID
            assert params == {"input": [{"type": "text", "text": TEXT}]}
            assert on_accepted is None
            return {"id": "new-turn", "status": "completed"}

    link = SimpleNamespace(require=AsyncMock(return_value=Server()))
    result = asyncio.run(codex_chat.send_codex_chat(link, IDENTITY, TEXT))
    assert result["last_status"] == "completed"
    assert result["messages"] == [
        {"id": "user", "role": "user", "text": TEXT},
        {"id": "work", "role": "delegation", "text": "请核对文稿。"},
        {"id": "reply", "role": "codex", "text": "可以接着聊。"},
    ]


def test_first_send_recovery_uses_saved_input_in_the_same_thread(tmp_path):
    request = AsyncMock(side_effect=[
        {"thread": {"id": THREAD_ID, "cwd": str(tmp_path)}},
        {"nextCursor": None, "data": [{"status": "failed", "items": [
            {"type": "userMessage", "id": "first", "content": [{"type": "text", "text": TEXT}]},
        ]}]},
        {},
    ])
    server = SimpleNamespace(request=request, run_turn=AsyncMock(), writers=set(), reader=SimpleNamespace(done=lambda: False))
    link = SimpleNamespace(require=AsyncMock(return_value=server))
    accepted = AsyncMock()
    history = asyncio.run(codex_chat.send_codex_chat(link, IDENTITY, TEXT, accept_first=accepted))
    assert history["messages"] == [{"id": "first", "role": "user", "text": TEXT}]
    accepted.assert_awaited_once()
    server.run_turn.assert_not_awaited()


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
