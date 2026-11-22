from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest


@pytest.mark.parametrize("registration", [None, True, False], ids=["standalone", "registered", "refused"])
def test_codex_task_registers_native_thread_and_returns_a2a_result(tmp_path, monkeypatch, registration):
    pytest.importorskip("a2a")
    codex = pytest.importorskip("openai_codex")
    import httpx2
    from mcp.server import Server
    from mcp.types import CallToolResult, ListToolsResult, Tool
    from openai_codex.types import TurnStatus

    from scripts.a2a_codex_poc import adapter

    actions = []

    class CompletedThread:
        id = "018f93b3-1acc-7000-b468-473d655ff7be"

        async def run(self, message):
            actions.append("run")
            return SimpleNamespace(status=TurnStatus.completed, final_response="Fixed and verified.")

    class SignedInCodex:
        def __init__(self, config):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *error):
            return None

        async def thread_start(self, **options):
            return CompletedThread()

    monkeypatch.setattr(adapter, "AsyncCodex", SignedInCodex)

    async def list_tools(_context, _params):
        return ListToolsResult(tools=[Tool(name="get_context", inputSchema={"type": "object"})])

    async def register(context, params):
        assert context.request.headers["authorization"] == "Bearer test"
        assert params.name == "get_context"
        actions.append(params.meta["threadId"])
        return CallToolResult(content=[], structuredContent={"ok": registration})

    mcp_app = Server("konden test", on_list_tools=list_tools, on_call_tool=register).streamable_http_app(
        streamable_http_path="/mcp/", stateless_http=True, json_response=True,
    )
    http_client = httpx2.AsyncClient

    def local_http_client(**kwargs):
        return http_client(transport=httpx2.ASGITransport(app=mcp_app), **kwargs)

    monkeypatch.setattr(adapter.httpx2, "AsyncClient", local_http_client)
    config = tmp_path / "konden.toml"
    config.write_text(
        '[mcp_servers.konden]\nurl = "http://127.0.0.1:8765/mcp/"\n\n'
        '[mcp_servers.konden.http_headers]\nAuthorization = "Bearer test"\n', encoding="utf-8",
    )
    connection = None if registration is None else adapter.KondenConnection.from_toml(config)

    async def submit():
        app = adapter.create_app(tmp_path, 48781, "test", codex.CodexConfig(), connection)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:48781",
        ) as client:
            response = await client.post(
                "/",
                headers={"Authorization": "Bearer test", "A2A-Version": "1.0"},
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "SendMessage",
                    "params": {"message": {
                        "messageId": "codex-poc-task",
                        "role": "ROLE_USER",
                        "parts": [{"text": "Fix a failed test."}],
                    }},
                },
            )
            response.raise_for_status()
            return response.json()["result"]["task"]

    async def run():
        async with mcp_app.router.lifespan_context(mcp_app):
            return await submit()

    task = asyncio.run(run())
    assert task["id"]
    if registration is False:
        assert actions == [CompletedThread.id]
        assert task["status"]["state"] == "TASK_STATE_FAILED"
        assert "登记失败" in task["status"]["message"]["parts"][0]["text"]
        return

    assert actions == (["run"] if registration is None else [CompletedThread.id, "run"])
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == "Fixed and verified."
