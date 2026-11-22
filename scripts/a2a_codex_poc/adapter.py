"""Serve a local Codex worker over A2A 1.0."""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from hmac import compare_digest
from pathlib import Path
import tomllib
from urllib.parse import urlsplit

from a2a.helpers import new_task
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    HTTPAuthSecurityScheme,
    Part,
    SecurityRequirement,
    SecurityScheme,
    TaskState,
)
from a2a.utils.errors import (
    InvalidParamsError,
    TaskNotCancelableError,
    UnsupportedOperationError,
)
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, ExternalMessage, Sandbox
from openai_codex.types import TurnStatus
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import uvicorn


@dataclass(frozen=True)
class KondenConnection:
    url: str
    authorization: str = field(repr=False)

    @classmethod
    def from_toml(cls, path: Path) -> KondenConnection:
        config = tomllib.loads(path.read_text(encoding="utf-8-sig"))["mcp_servers"]["konden"]
        url, authorization = config["url"], config["http_headers"]["Authorization"]
        if not isinstance(url, str) or not isinstance(authorization, str):
            raise ValueError("Codex 接入 URL 和 Authorization 必须是字符串")
        endpoint = urlsplit(url)
        if endpoint.scheme not in {"http", "https"} or not endpoint.hostname:
            raise ValueError("Codex 接入 URL 必须是 HTTP 或 HTTPS 地址")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise ValueError("Codex 接入需要 Bearer 凭据")
        return cls(url, authorization)

    async def register(self, thread_id: str) -> bool:
        async with httpx2.AsyncClient(headers={"Authorization": self.authorization}, timeout=10) as http:
            transport = streamable_http_client(self.url, http_client=http)
            async with Client(transport, read_timeout_seconds=10) as client:
                result = await client.call_tool("get_context", meta={"threadId": thread_id})
        if result.is_error:
            return False
        payload = result.structured_content
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise ValueError("konden get_context 未返回有效的登记结果")
        return payload["ok"]


class CodexExecutor(AgentExecutor):
    def __init__(self, workspace: Path, codex_config: CodexConfig, connection: KondenConnection | None) -> None:
        self.workspace = workspace
        self.codex_config = codex_config
        self.connection = connection

    async def execute(self, context: RequestContext, events: EventQueue) -> None:
        if context.current_task is not None:
            raise UnsupportedOperationError(message="请为后续工作提交一项新任务。")
        message = context.message
        if message is None or any(not part.HasField("text") for part in message.parts):
            raise InvalidParamsError(message="请用文本说明要交给 Codex 的任务。")
        brief = context.get_user_input().strip()
        if not brief:
            raise InvalidParamsError(message="任务说明不能为空。")

        task_id, context_id = context.task_id, context.context_id
        assert task_id is not None and context_id is not None
        await events.enqueue_event(new_task(
            task_id, context_id, TaskState.TASK_STATE_SUBMITTED, history=[message],
        ))
        updater = TaskUpdater(events, task_id, context_id)
        await updater.start_work()
        async with AsyncCodex(self.codex_config) as codex:
            thread = await codex.thread_start(
                cwd=str(self.workspace),
                sandbox=Sandbox.workspace_write,
                approval_mode=ApprovalMode.deny_all,
                developer_instructions=(
                    "处理 konden 提交的代码协作任务。工作范围是当前目录。"
                    "结束时说明实际改动、已执行的验证和未验证范围。"
                ),
            )
            if self.connection is not None and not await self.connection.register(thread.id):
                await updater.failed(updater.new_agent_message([Part(text="Codex 外部会话登记失败，请检查 konden 接入配置。")]))
                return
            result = await thread.run(ExternalMessage(tool_name="konden", content=brief))

        if result.final_response:
            await updater.add_artifact([Part(text=result.final_response)], name="Codex 报告")
        if result.status == TurnStatus.completed:
            await updater.complete()
        elif result.status == TurnStatus.interrupted:
            await updater.cancel()
        elif result.status == TurnStatus.failed:
            detail = result.error.message if result.error else "Codex 报告执行失败。"
            await updater.failed(updater.new_agent_message([Part(text=detail)]))
        else:
            raise RuntimeError(f"Codex returned a non-terminal turn: {result.status}")

    async def cancel(self, context: RequestContext, events: EventQueue) -> None:
        raise TaskNotCancelableError(message="这个 Codex 接入暂不支持取消任务。")


def create_app(
    workspace: Path, port: int, token: str, codex_config: CodexConfig, connection: KondenConnection | None = None,
) -> Starlette:
    card = AgentCard(
        name="Codex",
        description="在服务启动时选定的本机目录处理代码任务并回传报告。",
        version="0.1.0",
        supported_interfaces=[AgentInterface(
            url=f"http://127.0.0.1:{port}/", protocol_binding="JSONRPC", protocol_version="1.0",
        )],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(
            id="coding", name="代码任务", description="根据任务要求检查或修改代码并验证。",
            tags=["coding"],
        )],
        security_schemes={"local": SecurityScheme(
            http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="bearer"),
        )},
        security_requirements=[SecurityRequirement(schemes={"local": {}})],
    )
    handler = DefaultRequestHandler(
        agent_executor=CodexExecutor(workspace, codex_config, connection),
        task_store=InMemoryTaskStore(),
        agent_card=card,
    )
    expected = f"Bearer {token}".encode()

    async def authorize(request, call_next):
        if request.url.path == "/" and not compare_digest(
            request.headers.get("authorization", "").encode(), expected,
        ):
            return JSONResponse({"detail": "需要本机接入凭据。"}, status_code=401,
                                headers={"WWW-Authenticate": "Bearer"})
        return await call_next(request)

    return Starlette(
        routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")],
        middleware=[Middleware(BaseHTTPMiddleware, dispatch=authorize)],
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--windows-mxc", action="store_true", help="为本次 Codex 运行选择 Windows MXC 沙箱")
    parser.add_argument("--konden-mcp-config", type=Path, help="使用 konden 导出的 Codex 接入 TOML 登记外部会话")
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    if not workspace.is_dir():
        parser.error("--workspace 必须是现有目录")
    token = args.token_file.read_text(encoding="utf-8").strip()
    if not token:
        parser.error("--token-file 必须包含本次接入凭据")
    connection = None
    if args.konden_mcp_config is not None:
        try:
            connection = KondenConnection.from_toml(args.konden_mcp_config)
        except (KeyError, OSError, TypeError, ValueError):
            parser.error("--konden-mcp-config 必须是可读取的 konden Codex 接入 TOML 文件")
    overrides = ("windows.sandbox=mxc",) if args.windows_mxc else ()
    app = create_app(workspace, args.port, token, CodexConfig(config_overrides=overrides), connection)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
