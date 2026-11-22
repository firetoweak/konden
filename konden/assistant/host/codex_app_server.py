"""Host-owned connection to the authenticated loopback Codex App Server."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from hashlib import sha256
import json
import os
import re
from secrets import token_urlsafe
import subprocess

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from konden.assistant.host.codex_continuation import default_codex_executable

DISCONNECTED = "Codex 连接已断开。请在外部会话中重新连接。"


class CodexRequestError(RuntimeError):
    def __init__(self, method, error):
        self.error = error
        super().__init__(f"Codex {method}: {error}")


class CodexDisconnected(ValueError):
    pass


class CodexAppServer:
    def __init__(self, socket):
        self.socket = socket
        self.request_id = 0
        self.pending = {}
        self.turns = {}
        self.writers: set[str] = set()
        self.reader = asyncio.create_task(self._receive(), name="codex-websocket")

    async def write(self, message):
        try:
            await self.socket.send(json.dumps(message, ensure_ascii=False))
        except ConnectionClosed as error:
            raise CodexDisconnected(DISCONNECTED) from error

    async def _receive(self):
        try:
            async for frame in self.socket:
                message = json.loads(frame)
                if "method" not in message:
                    pending = self.pending.get(message["id"])
                    if pending is not None and not pending.done():
                        pending.set_result(message)
                elif "id" in message:
                    await self.write({"id": message["id"], "error": {
                        "code": -32601, "message": "This client does not support interactive tool requests",
                    }})
                elif message["method"] == "turn/completed":
                    params = message["params"]
                    pending = self.turns.get(params["threadId"])
                    if pending is not None and not pending.done():
                        pending.set_result(params["turn"])
        except (ConnectionClosed, CodexDisconnected):
            return
        finally:
            for pending in (*self.pending.values(), *self.turns.values()):
                if not pending.done():
                    pending.set_exception(CodexDisconnected(DISCONNECTED))

    async def request(self, method, params):
        request_id = self.request_id
        self.request_id += 1
        pending = asyncio.get_running_loop().create_future()
        self.pending[request_id] = pending
        try:
            async with asyncio.timeout(15):
                await self.write({"id": request_id, "method": method, "params": params})
                response = await pending
        finally:
            self.pending.pop(request_id)
            pending.cancel()
            if not pending.cancelled():
                pending.exception()
        if "error" in response:
            raise CodexRequestError(method, response["error"])
        return response["result"]

    async def run_turn(self, thread_id, params, *, timeout):
        if thread_id in self.turns:
            raise CodexRequestError("turn/start", {
                "code": -32600, "message": f"thread {thread_id} already has an active writer",
            })
        done = asyncio.get_running_loop().create_future()
        self.turns[thread_id] = done
        try:
            started = await self.request("turn/start", {"threadId": thread_id, **params})
            async with asyncio.timeout(timeout):
                terminal = await done
            if terminal["id"] != started["turn"]["id"]:
                raise ValueError("Codex returned a different turn")
            return terminal
        finally:
            self.turns.pop(thread_id)
            done.cancel()
            if not done.cancelled():
                done.exception()


async def _listener(stderr):
    async for line in stderr:
        match = re.search(rb"listening on: (ws://127\.0\.0\.1:\d+)", line)
        if match is not None:
            return match[1].decode("ascii")
    raise CodexDisconnected("本机 Codex 未能启动 WebSocket，请确认 Codex 已安装并支持 App Server。")


async def _drain(reader):
    while await reader.read(8192):
        pass


@asynccontextmanager
async def open_codex_app_server(executable):
    token = token_urlsafe(32)
    process = await asyncio.create_subprocess_exec(
        executable, "app-server", "-c", "thread_unload_delay_secs=0",
        "--listen", "ws://127.0.0.1:0", "--ws-auth", "capability-token",
        "--ws-token-sha256", sha256(token.encode()).hexdigest(), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    stderr = None
    try:
        async with asyncio.timeout(15):
            url = await _listener(process.stderr)
        stderr = asyncio.create_task(_drain(process.stderr))
        async with connect(url, additional_headers={"Authorization": f"Bearer {token}"}, proxy=None,
                           open_timeout=5, close_timeout=2, ping_interval=10, ping_timeout=5,
                           max_size=16 * 1024 * 1024) as socket:
            server = CodexAppServer(socket)
            try:
                await server.request("initialize", {"clientInfo": {"name": "konden", "version": "0.1.0"}})
                await server.write({"method": "initialized", "params": {}})
                yield server
            finally:
                await socket.close()
                await server.reader
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        if stderr is None:
            await process.communicate()
        else:
            await asyncio.gather(process.wait(), stderr)


class CodexLink:
    def __init__(self, changed):
        self.changed = changed
        self.error = None
        self._server = None
        self._ready = None
        self._task = None

    @property
    def current(self):
        if self._server is not None and not self._server.reader.done():
            return self._server
        return None

    def view(self):
        if self.current is not None:
            status = "connected"
        elif self._ready is not None and not self._ready.done():
            status = "connecting"
        else:
            status = "disconnected"
        return {"status": status, "error": self.error}

    async def require(self):
        if self.current is not None:
            return self.current
        if self._ready is None or not self._ready.done():
            return await self.reconnect()
        raise CodexDisconnected(self.error or DISCONNECTED)

    async def reconnect(self):
        if self.current is not None:
            return self.current
        if self._ready is not None and not self._ready.done():
            return await asyncio.shield(self._ready)
        if self._task is not None and not self._task.done():
            await asyncio.shield(self._task)
            return await self.reconnect()
        self.error = None
        self._ready = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._run(self._ready), name="codex-app-server")
        return await asyncio.shield(self._ready)

    async def _run(self, ready):
        try:
            await self.changed()
            try:
                async with open_codex_app_server(default_codex_executable()) as server:
                    self._server = server
                    ready.set_result(server)
                    await self.changed()
                    await server.reader
                self.error = DISCONNECTED
            except (OSError, TimeoutError, InvalidHandshake, CodexDisconnected) as error:
                self.error = str(error) if isinstance(error, CodexDisconnected) else "无法连接本机 Codex，请检查安装后重试。"
                if not ready.done():
                    ready.set_exception(CodexDisconnected(self.error))
        except Exception as error:
            if not ready.done():
                ready.set_exception(error)
            raise
        finally:
            self._server = None
            if not ready.done():
                ready.cancel()
            await self.changed()

    async def close(self):
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
