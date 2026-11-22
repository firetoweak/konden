"""One local request stream to Codex's App Server."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
import json
import os
import subprocess


class CodexRequestError(RuntimeError):
    def __init__(self, method, error):
        self.error = error
        super().__init__(f"Codex {method}: {error}")


class CodexDisconnected(RuntimeError):
    pass


async def _stderr_tail(reader):
    tail = b""
    while chunk := await reader.read(8192):
        tail = (tail + chunk)[-8192:]
    return tail.decode("utf-8", errors="replace").strip()


class CodexAppServer:
    def __init__(self, process, stderr):
        self.process = process
        self.stderr = stderr
        self.request_id = 0
        self.completed = {}

    async def write(self, message):
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()

    async def receive(self):
        line = await self.process.stdout.readline()
        if not line:
            raise CodexDisconnected(await self.stderr or "Codex exited before replying")
        return json.loads(line)

    async def handle_event(self, message):
        if "method" not in message:
            raise ValueError("Codex returned an unexpected response while waiting for a turn")
        if "id" in message:
            await self.write({"id": message["id"], "error": {
                "code": -32601, "message": "This client does not support interactive tool requests",
            }})
        elif message["method"] == "turn/completed":
            params = message["params"]
            turn = params["turn"]
            self.completed[(params["threadId"], turn["id"])] = turn

    async def request(self, method, params):
        request_id = self.request_id
        self.request_id += 1
        await self.write({"id": request_id, "method": method, "params": params})
        while True:
            response = await self.receive()
            if "method" in response:
                await self.handle_event(response)
                continue
            if response["id"] != request_id:
                raise ValueError("Codex replied with a different request id")
            if "error" in response:
                raise CodexRequestError(method, response["error"])
            return response["result"]

    async def wait_for_turn(self, thread_id, turn_id):
        key = (thread_id, turn_id)
        while key not in self.completed:
            await self.handle_event(await self.receive())
        return self.completed.pop(key)


@asynccontextmanager
async def open_codex_app_server(executable):
    process = await asyncio.create_subprocess_exec(
        executable, "app-server", "--stdio",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, limit=16 * 1024 * 1024,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    stderr = asyncio.create_task(_stderr_tail(process.stderr))
    server = CodexAppServer(process, stderr)
    try:
        async with asyncio.timeout(15):
            await server.request("initialize", {"clientInfo": {"name": "konden", "version": "0.1.0"}})
            await server.write({"method": "initialized", "params": {}})
        yield server
    finally:
        process.stdin.close()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
        await process.wait()
        await stderr
