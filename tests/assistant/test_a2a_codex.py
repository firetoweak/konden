from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

from PIL import Image
import pytest

from konden.assistant.a2a import (
    A2A_COMPLETED,
    A2A_DELEGATE,
    A2A_LIST,
    A2A_TOOLSET,
    A2AToolset,
)
from konden.assistant.host.codex_delegation import CodexDelegation
from konden.paths import KondenHome

THREAD_ID = "12345678-abcd-7123-abcd-123456789abc"
IDENTITY = UUID(THREAD_ID).hex


class NativeCodex:
    def __init__(self, workspace, produce):
        self.workspace = workspace
        self.produce = produce
        self.writer = None
        self.methods = []
        self.writers = set()
        self.reader = SimpleNamespace(done=lambda: False)

    async def request(self, method, params):
        self.methods.append(method)
        if method == "thread/start":
            assert "developerInstructions" in params
            self.writer = Path(params["cwd"])
            return {"thread": {"id": THREAD_ID, "cwd": str(self.writer)}}
        assert params["threadId"] == THREAD_ID
        if method == "thread/resume":
            assert params["excludeTurns"]
            self.writer = self.workspace
            return {"thread": {"id": THREAD_ID, "cwd": str(self.writer), "name": "原生标题"}}
        if method == "thread/name/set":
            assert params["name"] == "图和数据的会话"
            return {}
        if method == "thread/unsubscribe":
            return {}
        assert method == "thread/turns/list"
        report = "已生成图和数据。" if "thread/resume" in self.methods else "# 图和数据的会话\n\n已生成图和数据。"
        return {"data": [{"id": "turn", "items": [{"type": "agentMessage", "phase": "final_answer", "text": report}]}]}

    async def run_turn(self, thread_id, params, *, timeout, on_accepted):
        assert thread_id == THREAD_ID and params["input"] == []
        output = params["toolOutput"]
        assert output["name"] == "a2a_delegation"
        await on_accepted()
        paths = output["output"].partition("相对路径交付文件：")[2].split("\n", 1)[0].removesuffix("。")
        self.produce([self.writer / name for name in paths.split("、")])
        return {"id": "turn", "status": "completed"}


def delegation(tmp_path, produce):
    server = NativeCodex(tmp_path, produce)
    external = SimpleNamespace(
        has_local_codex=Mock(side_effect=lambda identity: identity == IDENTITY),
        codex_link=SimpleNamespace(require=AsyncMock(return_value=server)),
        bind_local_codex=AsyncMock(), notify_changed=AsyncMock(),
    )
    home = KondenHome(tmp_path)
    return CodexDelegation(home, tmp_path / "r", external), external, server


@pytest.mark.parametrize("use_existing_chat", [False, True])
def test_a2a_delivery_uses_the_selected_native_conversation(tmp_path, use_existing_chat):
    # A process executing in an existing chat must never deliver a stale file from that directory.
    def produce(files):
        if use_existing_chat:
            assert files[0].parent.parent == tmp_path
        Image.new("RGB", (20, 10), color="blue").save(files[0])
        files[1].write_bytes(b"day,count\nFri,10\n")

    codex, external, server = delegation(tmp_path, produce)

    result = asyncio.run(codex.delegate("coordinator", {
        "brief": "交付图和数据", "output_files": ["chart.png", "data.csv"],
        "codex_session_id": IDENTITY if use_existing_chat else None,
    }))

    assert result["code"] == A2A_COMPLETED
    assert result["data"]["report"] == "已生成图和数据。"
    assert result["data"]["codex_thread_id"] == THREAD_ID
    if use_existing_chat:
        assert result["data"]["codex_session_name"] == "原生标题"
        assert server.methods == ["thread/resume", "thread/turns/list", "thread/unsubscribe"]
        external.bind_local_codex.assert_not_awaited()
        assert not codex.workspaces.exists()
    else:
        assert result["data"]["codex_session_name"] == "图和数据的会话"
        assert server.methods == ["thread/start", "thread/turns/list", "thread/name/set", "thread/unsubscribe"]
        external.bind_local_codex.assert_awaited_once_with(IDENTITY)
    image, csv = result["data"]["files"]
    assert [image["name"], csv["name"]] == ["chart.png", "data.csv"]
    store = codex.attachments.for_session("coordinator")
    assert store.read(csv["attachment_id"]) == b"day,count\nFri,10\n"
    with Image.open(store.path(image["attachment_id"])) as preview:
        assert preview.size == (20, 10)
    assert not asyncio.run(codex.attachments.available_in_session("another", csv["attachment_id"]))


@pytest.mark.parametrize("use_existing_chat", [False, True])
def test_a2a_keeps_accepted_conversation_when_delivery_files_are_missing(tmp_path, use_existing_chat):
    def produce(files):
        assert external.bind_local_codex.await_count == (0 if use_existing_chat else 1)
        files[0].write_bytes(b"1,2")

    (tmp_path / "missing.csv").write_bytes(b"stale")
    codex, external, _ = delegation(tmp_path, produce)
    result = asyncio.run(codex.delegate("coordinator", {
        "brief": "交付数据", "output_files": ["first.csv", "missing.csv"],
        "codex_session_id": IDENTITY if use_existing_chat else None,
    }))

    assert result["code"] == "A2A_TASK_FAILED"
    assert "missing.csv" in result["error"]
    assert result["data"]["codex_thread_id"] == THREAD_ID
    assert not codex.attachments.for_session("coordinator").files.originals.exists()


def test_a2a_toolset_replaces_the_old_bot_registration_contract():
    transport = AsyncMock(return_value={"ok": True})
    toolset = A2AToolset(transport, "coordinator")
    assert toolset.restore(A2A_TOOLSET.id, 1, (Mock(),)) == ()
    listing, delegate = asyncio.run(toolset.load(A2A_TOOLSET.id, A2A_TOOLSET.revision))
    assert listing.name == A2A_LIST == "list_codex_sessions"
    assert delegate.name == A2A_DELEGATE
    assert "bot_id" not in delegate.parameters["properties"]
    assert asyncio.run(delegate.execute({"brief": "核对稿件"}))["ok"]
    assert transport.await_args.args[2]["codex_session_id"] is None
