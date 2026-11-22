"""Native titles are optional display metadata, never conversation identity or history."""
import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from konden.assistant.host.codex_app_server import (
    CodexDisconnected,
    CodexLink,
    CodexRequestError,
)
from konden.assistant.host.codex_titles import read_codex_titles

IDENTITY = "1" * 32
THREAD_ID = str(UUID(IDENTITY))


def test_missing_local_codex_or_unset_title_does_not_invent_a_name():
    assert asyncio.run(read_codex_titles([IDENTITY], server=None)) == {}
    request = AsyncMock(side_effect=[
        {"thread": {"id": THREAD_ID, "name": None}},
        CodexRequestError("thread/read", {"code": -32600, "message": f"thread not loaded: {THREAD_ID}"}),
        CodexDisconnected("closed"),
    ])
    for _ in range(3):
        assert asyncio.run(read_codex_titles([IDENTITY], server=SimpleNamespace(request=request))) == {}


@pytest.mark.parametrize("response, error", [
    ({"thread": {"id": str(UUID("2" * 32)), "name": "另一聊天"}}, "different thread"),
    (CodexRequestError("thread/read", {"code": -32603, "message": "database damaged"}), "database damaged"),
])
def test_wrong_identity_and_unknown_errors_are_not_hidden_as_missing_titles(response, error):
    server = SimpleNamespace(request=AsyncMock(side_effect=[response]))
    with pytest.raises((ValueError, RuntimeError), match=error):
        asyncio.run(read_codex_titles([IDENTITY], server=server))


@pytest.mark.process
def test_websocket_title_lookup_and_reconnect_use_the_same_chat(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("konden.assistant.host.codex_app_server.default_codex_executable", lambda: sys.executable)
    (tmp_path / "app-server").write_text('''
import asyncio, hashlib, http, json, pathlib, sys
from websockets.asyncio.server import serve

assert sys.argv[1:-1] == ["-c", "thread_unload_delay_secs=0", "--listen", "ws://127.0.0.1:0",
                         "--ws-auth", "capability-token", "--ws-token-sha256"]

def authenticate(connection, request):
    authorization = request.headers.get("Authorization", "")
    prefix, _, token = authorization.partition(" ")
    if prefix != "Bearer" or hashlib.sha256(token.encode()).hexdigest() != sys.argv[-1]:
        return connection.respond(http.HTTPStatus.UNAUTHORIZED, "Unauthorized")

async def handler(socket):
    async for line in socket:
        request = json.loads(line)
        method = request["method"]
        with pathlib.Path("calls.jsonl").open("a", encoding="utf-8") as log:
            print(json.dumps(request), file=log)
        if method == "initialized":
            continue
        if method == "initialize":
            result = {}
        else:
            assert method == "thread/read"
            assert request["params"]["includeTurns"] is False
            result = {"thread": {"id": request["params"]["threadId"], "name": "原生标题"}}
        await socket.send(json.dumps({"id": request["id"], "result": result}))

async def main():
    async with serve(handler, "127.0.0.1", 0, process_request=authenticate) as server:
        print(f"listening on: ws://127.0.0.1:{server.sockets[0].getsockname()[1]}", file=sys.stderr, flush=True)
        await server.serve_forever()

asyncio.run(main())
''', encoding="utf-8")

    async def exercise():
        states = []

        async def changed():
            states.append(link.view()["status"])

        link = CodexLink(changed)
        try:
            first = await link.require()
            assert await link.require() is first
            assert await read_codex_titles([IDENTITY, "2" * 32, IDENTITY], server=first) == {
                IDENTITY: "原生标题", "2" * 32: "原生标题",
            }
            await first.socket.close()
            await first.reader
            assert link.view()["status"] == "disconnected"
            with pytest.raises(CodexDisconnected):
                await link.require()
            second = await link.reconnect()
            assert second is not first
            assert await read_codex_titles([IDENTITY], server=second) == {IDENTITY: "原生标题"}
        finally:
            await link.close()
        assert states == ["connecting", "connected", "disconnected", "connecting", "connected", "disconnected"]

    asyncio.run(exercise())
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [item["method"] for item in calls] == [
        "initialize", "initialized", "thread/read", "thread/read", "initialize", "initialized", "thread/read",
    ]
    assert calls[2]["params"] == calls[-1]["params"]
