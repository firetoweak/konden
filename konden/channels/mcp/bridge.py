"""Present Konden's MCP tools to Codex over stdio and carry calls over WebSocket."""

import argparse
import asyncio
from pathlib import Path

from mcp import ClientSession
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from mcp.types import (
    CONNECTION_CLOSED,
    REQUEST_TIMEOUT,
    CallToolRequest,
    CallToolResult,
    TextContent,
)
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake
from websockets.protocol import State

from konden.assistant.external_messages import ConnectionStore
from konden.channels.mcp import tool_server
from konden.channels.mcp.socket_streams import socket_streams


class ConnectionUnavailable(ValueError):
    pass


class CollaborationLink:
    def __init__(self, address: str, connections: ConnectionStore, connection_id: str):
        self.address = address
        self.connections = connections
        self.connection_id = connection_id
        self._socket = None
        self._task = None
        self._ready = None

    async def session(self):
        if self._socket is not None and self._socket.state is not State.OPEN:
            await asyncio.shield(self._task)
        if self._task is None or self._task.done():
            if self._task is not None:
                self._task.result()
            self._ready = asyncio.get_running_loop().create_future()
            self._task = asyncio.create_task(self._connect(self._ready), name="konden-collaboration")
        result = await asyncio.shield(self._ready)
        if isinstance(result, ConnectionUnavailable):
            raise result
        return result

    async def _connect(self, ready):
        try:
            credential = self.connections.get(self.connection_id)
            if credential is None:
                ready.set_result(ConnectionUnavailable("Konden 接入已撤销。请在 Konden 重新安装本机 Codex 接入。"))
                return
            try:
                async with connect(self.address, additional_headers={"Authorization": f"Bearer {credential.token}"},
                                   proxy=None, open_timeout=4, close_timeout=2, max_size=64 * 1024 * 1024) as socket:
                    self._socket = socket
                    async with socket_streams(socket.recv, socket.send, (ConnectionClosed,)) as streams, \
                            ClientSession(*streams, read_timeout_seconds=5) as session:
                        try:
                            await session.initialize()
                        except MCPError as error:
                            if error.code not in {CONNECTION_CLOSED, REQUEST_TIMEOUT}:
                                raise
                            ready.set_result(ConnectionUnavailable("Konden 尚未确认连接，请重试。"))
                            return
                        ready.set_result(session)
                        await socket.wait_closed()
            except (OSError, TimeoutError, InvalidHandshake, ConnectionClosed) as error:
                if not ready.done():
                    detail = ("Konden 未接受接入。请在 Konden 检查本机 Codex 接入。" if isinstance(error, InvalidHandshake)
                              else "无法连接 Konden。请启动 Konden 后再次调用。")
                    ready.set_result(ConnectionUnavailable(detail))
        except Exception as error:
            if ready.done():
                raise
            ready.set_exception(error)
        finally:
            self._socket = None
            if not ready.done():
                ready.cancel()

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def call(self, context, params):
        try:
            session = await self.session()
        except ConnectionUnavailable as error:
            return _error("COLLABORATION_DISCONNECTED", str(error), ok=False)
        try:
            return await session.send_request(CallToolRequest(params=params), CallToolResult,
                                              request_read_timeout_seconds=600)
        except MCPError as error:
            if error.code not in {CONNECTION_CLOSED, REQUEST_TIMEOUT}:
                raise
            return _error("COLLABORATION_RESULT_UNKNOWN", "未收到 Konden 的结果确认。请先查看原消息或任务回执，避免重复发送。", ok=None)


def _error(code: str, message: str, *, ok: bool | None):
    return CallToolResult(content=[TextContent(type="text", text=message)],
                          structuredContent={"ok": ok, "code": code, "error": message}, isError=True)


async def serve(address: str, store: Path, connection_id: str):
    link = CollaborationLink(address, ConnectionStore(store), connection_id)
    server = tool_server(link.call)
    try:
        async with stdio_server() as streams:
            await server.run(*streams, server.create_initialization_options())
    finally:
        await link.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("address")
    parser.add_argument("store", type=Path)
    parser.add_argument("connection_id")
    args = parser.parse_args()
    asyncio.run(serve(args.address, args.store, args.connection_id))


if __name__ == "__main__":
    main()
