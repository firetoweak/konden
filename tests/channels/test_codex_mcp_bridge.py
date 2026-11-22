"""A live stdio client can survive a lost WebSocket acknowledgement without replaying it."""

import asyncio
import json
from pathlib import Path
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import pytest
from websockets.asyncio.server import serve

from konden.assistant.external_messages import ConnectionStore
from konden.channels.mcp.install import BRIDGE_MODULE


@pytest.mark.process
def test_stdio_keeps_native_metadata_and_reauthenticates_without_replaying(tmp_path):
    connections = ConnectionStore(tmp_path / "access.json")
    credential, _ = connections.create()
    identity = "01234567-89ab-7def-8123-456789abcdef"
    handshakes = []
    calls = []

    async def peer(socket):
        handshakes.append(socket.request.headers.get("Authorization"))
        if len(handshakes) == 1:
            await socket.close()
            return
        async for frame in socket:
            request = json.loads(frame)
            if request["method"] == "initialize":
                result = {"protocolVersion": request["params"]["protocolVersion"], "capabilities": {"tools": {}},
                          "serverInfo": {"name": "test", "version": "1"}}
            elif request["method"] == "notifications/initialized":
                continue
            else:
                args = request["params"]
                calls.append((args["name"], args["_meta"]["threadId"]))
                if args["name"] == "send_message":
                    # The server has received the call, but the client will never see its reply.
                    await socket.close()
                    return
                result = {"content": [{"type": "text", "text": identity}], "structuredContent": {"identity": identity}}
            await socket.send(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}))

    async def exercise():
        async with serve(peer, "127.0.0.1", 0) as server:
            address = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/mcp/ws"
            stdio = StdioServerParameters(
                command=sys.executable,
                args=["-m", BRIDGE_MODULE, address, str(connections.path), credential.connection_id],
                env={"PYTHONPATH": str(Path(__file__).resolve().parents[2])},
                cwd=tmp_path,
            )
            async with stdio_client(stdio) as streams, ClientSession(*streams, read_timeout_seconds=10) as client:
                await client.initialize()
                assert {tool.name for tool in (await client.list_tools()).tools} >= {"get_context", "delegate_a2a_task"}
                meta = {"threadId": identity}
                disconnected = await client.call_tool("get_context", {}, meta=meta)
                assert disconnected.structured_content["code"] == "COLLABORATION_DISCONNECTED"
                assert (await client.call_tool("get_context", {}, meta=meta)).structured_content == {"identity": identity}
                first = await client.call_tool("send_message", {"recipient": "Bot", "text": "check", "delivery_id": "once"}, meta=meta)
                assert first.structured_content["ok"] is None
                assert first.structured_content["code"] == "COLLABORATION_RESULT_UNKNOWN"
                assert (await client.call_tool("get_context", {}, meta=meta)).structured_content == {"identity": identity}

    asyncio.run(asyncio.wait_for(exercise(), timeout=30))
    assert calls == [("get_context", identity), ("send_message", identity), ("get_context", identity)]
    assert handshakes == [f"Bearer {credential.token}"] * 3
