"""Run one delegated task over a scoped, standard A2A HTTP endpoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass, field
from hmac import compare_digest
from secrets import token_urlsafe
import socket

from a2a.client import A2AClientError, ClientConfig, create_client
from a2a.helpers import get_message_text, new_task, new_text_message
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
    GetTaskRequest,
    HTTPAuthSecurityScheme,
    Part,
    Role,
    SecurityRequirement,
    SecurityScheme,
    SendMessageRequest,
    Task,
    TaskState,
)
from a2a.utils.errors import InternalError, InvalidParamsError, TaskNotCancelableError
from google.protobuf.json_format import MessageToDict
import httpx
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import uvicorn

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


class WorkFailed(ValueError):
    """The task or delivery is known to have failed."""


@dataclass(frozen=True, slots=True)
class TaskDelivery:
    report: str
    parts: list[Part] = field(default_factory=list)
    metadata: dict[str, str] = field(default_factory=dict)


class ResultUnknown(RuntimeError):
    def __init__(self, task_id):
        self.task_id = task_id
        super().__init__("暂未确认这次 A2A 委派的结果。请先核对，避免重复派单。")


async def _send_task(url: str, token: str, message) -> Task:
    task_id = None
    try:
        async with asyncio.timeout(600):
            async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"},
                                         timeout=httpx.Timeout(15, read=600), trust_env=False) as http:
                async with await create_client(url, ClientConfig(httpx_client=http)) as client:
                    async for event in client.send_message(SendMessageRequest(message=message)):
                        if event.HasField("task"):
                            task_id = event.task.id
                    if not task_id:
                        raise ResultUnknown(None)
                    task = await client.get_task(GetTaskRequest(id=task_id))
    except (A2AClientError, InternalError, httpx.TransportError, TimeoutError) as error:
        raise ResultUnknown(task_id) from error
    if task.id != task_id:
        raise ValueError("A2A returned a different task")
    return task


class _TaskExecutor(AgentExecutor):
    def __init__(self, work_type, execute):
        self.work_type = work_type
        self.execute_work = execute
        self.running = None

    async def execute(self, context: RequestContext, events: EventQueue) -> None:
        message = context.message
        if context.current_task is not None or message is None or len(message.parts) != 1 or not message.parts[0].HasField("text"):
            raise InvalidParamsError(message="请用一条文字消息提交一项新任务。")
        try:
            work = self.work_type.model_validate({**MessageToDict(message.metadata), "brief": message.parts[0].text})
        except ValidationError as error:
            raise InvalidParamsError(message="任务说明或交付要求无效。") from error

        task_id, context_id = context.task_id, context.context_id
        assert task_id is not None and context_id is not None
        await events.enqueue_event(new_task(task_id, context_id, TaskState.TASK_STATE_SUBMITTED, history=[message]))
        updater = TaskUpdater(events, task_id, context_id)
        await updater.start_work()
        self.running = asyncio.current_task()
        try:
            delivery = await self.execute_work(task_id, work)
            await updater.add_artifact([Part(text=delivery.report.strip()[:6000])], name="任务报告", metadata=delivery.metadata)
            for part in delivery.parts:
                await updater.add_artifact([part], name=part.filename)
            await updater.complete()
        except WorkFailed as error:
            await updater.failed(updater.new_agent_message([Part(text=str(error))]))
        finally:
            self.running = None

    async def cancel(self, context: RequestContext, events: EventQueue) -> None:
        raise TaskNotCancelableError(message="此任务无法通过 A2A 请求取消。")


def _app(name, port, token, executor):
    card = AgentCard(
        name=name, description="执行任务并交付报告和文件。", version="0.1.0",
        supported_interfaces=[AgentInterface(
            url=f"http://127.0.0.1:{port}/", protocol_binding="JSONRPC", protocol_version="1.0",
        )],
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=["text/plain"], default_output_modes=["text/plain", "application/octet-stream"],
        skills=[AgentSkill(id="work", name="执行任务", description="按要求执行并交付报告或文件。", tags=["work"])],
        security_schemes={"local": SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="bearer"))},
        security_requirements=[SecurityRequirement(schemes={"local": {}})],
    )
    handler = DefaultRequestHandler(agent_executor=executor, task_store=InMemoryTaskStore(), agent_card=card)
    expected = f"Bearer {token}".encode()

    async def authorize(request, call_next):
        if not compare_digest(request.headers.get("authorization", "").encode(), expected):
            return JSONResponse({"detail": "需要本机接入凭据。"}, status_code=401)
        return await call_next(request)

    return Starlette(
        routes=[*create_agent_card_routes(card), *create_jsonrpc_routes(handler, "/")],
        middleware=[Middleware(BaseHTTPMiddleware, dispatch=authorize)],
    )


class _LoopbackServer(uvicorn.Server):
    def __init__(self, config):
        super().__init__(config)
        self.ready = asyncio.Event()

    @contextmanager
    def capture_signals(self):
        # The Web application owns the process signal handlers.
        yield

    async def startup(self, sockets=None):
        await super().startup(sockets=sockets)
        self.ready.set()


@asynccontextmanager
async def _endpoint(name, executor):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        token = token_urlsafe(32)
        server = _LoopbackServer(uvicorn.Config(_app(name, port, token, executor), log_config=None,
                                                access_log=False, lifespan="off", timeout_graceful_shutdown=2))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(5):
                await server.ready.wait()
            yield f"http://127.0.0.1:{port}", token
        finally:
            try:
                if (running := executor.running) is not None:
                    running.cancel()
                    with suppress(asyncio.CancelledError):
                        await running
            finally:
                server.should_exit = True
                await serving


async def run_loopback_task(name: str, work, execute) -> Task:
    message = new_text_message(work.brief, role=Role.ROLE_USER)
    message.metadata.update(work.model_dump(exclude={"brief"}, exclude_none=True))
    async with _endpoint(name, _TaskExecutor(type(work), execute)) as (url, token):
        task = await _send_task(url, token, message)
    if task.status.state == TaskState.TASK_STATE_FAILED:
        raise WorkFailed(get_message_text(task.status.message))
    if task.status.state != TaskState.TASK_STATE_COMPLETED:
        raise ResultUnknown(task.id)
    return task
