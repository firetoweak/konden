from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest


def test_codex_completed_turn_returns_a2a_report(tmp_path, monkeypatch):
    pytest.importorskip("a2a")
    codex = pytest.importorskip("openai_codex")
    from openai_codex.types import TurnStatus

    from scripts.a2a_codex_poc import adapter

    class CompletedThread:
        async def run(self, message):
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

    async def submit():
        app = adapter.create_app(tmp_path, 48781, "test", codex.CodexConfig())
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

    task = asyncio.run(submit())
    assert task["id"]
    assert task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert task["artifacts"][0]["parts"][0]["text"] == "Fixed and verified."
