"""Make the local Codex A2A endpoint available to a konden conversation."""
from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from pathlib import Path

from a2a.client import A2AClientError, ClientConfig, create_client
from a2a.helpers import get_message_text, new_text_message
from a2a.types import GetTaskRequest, Role, SendMessageRequest, Task, TaskState
from a2a.utils.errors import InternalError
import httpx
from mcp.server import MCPServer


def describe_task(task: Task) -> dict[str, str]:
    report = "\n".join(part.text for artifact in task.artifacts for part in artifact.parts)
    return {
        "task_id": task.id,
        "state": TaskState.Name(task.status.state),
        "detail": get_message_text(task.status.message),
        "report": report,
    }


def create_server(port: int, token: str) -> MCPServer:
    server = MCPServer("Codex", instructions="可以把用户交代的编码任务委托给本机 Codex。")

    @asynccontextmanager
    async def connect():
        async with httpx.AsyncClient(
            headers={"Authorization": f"Bearer {token}"}, timeout=180, trust_env=False,
        ) as http:
            async with await create_client(
                f"http://127.0.0.1:{port}", ClientConfig(httpx_client=http),
            ) as client:
                yield client

    @server.tool()
    async def delegate_to_codex(brief: str) -> dict[str, str]:
        """请 Codex 在接入时选定的目录处理代码任务。给出目标、相对路径和验收要求。返回任务编号及报告。"""
        request = SendMessageRequest(message=new_text_message(brief, role=Role.ROLE_USER))
        async with connect() as client:
            task_id = ""
            try:
                async for event in client.send_message(request):
                    if event.HasField("task"):
                        task_id = event.task.id
                if not task_id:
                    raise ValueError("Codex 没有返回任务编号，无法确认执行结果。")
                return describe_task(await client.get_task(GetTaskRequest(id=task_id)))
            except (A2AClientError, InternalError, httpx.TransportError):
                return {
                    "task_id": task_id,
                    "state": "unknown",
                    "detail": ("未收到确定结果。用任务编号查询当前任务。" if task_id else
                               "未收到任务编号。请在工作目录核对实际结果后再决定是否提交。"),
                }

    @server.tool()
    async def get_codex_task(task_id: str) -> dict[str, str]:
        """用 Codex 已返回的任务编号查看状态和报告。"""
        async with connect() as client:
            return describe_task(await client.get_task(GetTaskRequest(id=task_id)))

    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token-file", required=True, type=Path)
    args = parser.parse_args()
    token = args.token_file.read_text(encoding="utf-8").strip()
    if not token:
        parser.error("--token-file 必须包含本次接入凭据")
    create_server(args.port, token).run()


if __name__ == "__main__":
    main()
