"""Native Codex chat operations over the Host's WebSocket connection."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import UUID

from konden.assistant.a2a import A2A_CODEX_INPUT
from konden.assistant.external_messages import external_name
from konden.assistant.host.codex_app_server import CodexDisconnected, CodexRequestError
from konden.assistant.host.codex_projects import (
    CodexProjectTarget,
    assign_chat_projects,
    list_codex_projects,
    read_codex_project,
)
from konden.assistant.host.codex_titles import ThreadSummary

_GUI_PERMISSIONS = {"approvalPolicy": "never", "sandbox": "workspace-write"}
_BUSY = "Codex Desktop 或另一个窗口正在使用这段会话。请先结束当前操作，再在原会话继续。"


class CodexChatUnavailable(ValueError):
    pass


def _thread(result, expected_id=None):
    summary = ThreadSummary.model_validate(result["thread"])
    if expected_id is not None and summary.id != expected_id:
        raise ValueError("Codex replied with a different thread id")
    return summary


def _native_error(error, thread_id):
    if error.error == {"code": -32600, "message": f"thread {thread_id} already has an active writer"}:
        return CodexChatUnavailable(_BUSY)
    if error.error == {"code": -32600, "message": f"thread not loaded: {thread_id}"}:
        return CodexChatUnavailable("这条会话当前无法在本机 Codex 找到。请检查 Desktop 里的原会话。")
    return error


@asynccontextmanager
async def edit_codex_chat(server, *, identity: str | None = None, workspace: Path | None = None,
                          project_id: str | None = None, instructions: str | None = None):
    thread = None
    thread_id = str(UUID(identity)) if identity is not None else None
    if thread_id is not None:
        if thread_id in server.writers:
            raise CodexChatUnavailable(_BUSY)
        server.writers.add(thread_id)
    try:
        if thread_id is None:
            assert workspace is not None
            args = {"cwd": str(workspace), **_GUI_PERMISSIONS}
            if project_id is not None:
                args["projectId"] = project_id
            if instructions is not None:
                args["developerInstructions"] = instructions
            thread = _thread(await server.request("thread/start", args))
            thread_id = thread.id
            server.writers.add(thread_id)
        else:
            resumed = await server.request("thread/resume", {
                "threadId": thread_id, "excludeTurns": True, **_GUI_PERMISSIONS,
            })
            thread = _thread(resumed, thread_id)
            if resumed["thread"].get("status", {}).get("type") == "active":
                raise CodexChatUnavailable(_BUSY)
        yield thread
    except CodexRequestError as error:
        known = _native_error(error, thread_id)
        if known is error:
            raise
        raise known from error
    finally:
        if thread_id is not None:
            try:
                if thread is not None and not server.reader.done():
                    with suppress(CodexDisconnected):
                        await server.request("thread/unsubscribe", {"threadId": thread_id})
            finally:
                server.writers.remove(thread_id)


async def prepare_codex_chat(link, workspace: Path | None, *, project: CodexProjectTarget | None = None) -> str:
    server = await link.require()
    if project is not None:
        native_project = await read_codex_project(server, project.id)
        workspace = await asyncio.to_thread(native_project.working_directory, project.root)
    async with edit_codex_chat(server, workspace=workspace, project_id=project.id if project else None) as thread:
        if project is not None and thread.project_id != project.id:
            raise ValueError("Codex replied with a different thread project")
        # The first send may have to resume this ID after a reconnect.
        await server.request("thread/inject_items", {"threadId": thread.id, "items": [{
            "type": "message", "role": "developer", "content": [{
                "type": "input_text", "text": "这段 Codex 会话由用户在 Konden 创建。",
            }],
        }]})
        return UUID(thread.id).hex


async def native_codex_thread(server, identity):
    thread_id = str(UUID(identity))
    try:
        return _thread(await server.request("thread/read", {"threadId": thread_id, "includeTurns": False}), thread_id)
    except CodexRequestError as error:
        known = _native_error(error, thread_id)
        if known is error:
            raise
        raise known from error


async def list_codex_chats(link, query):
    server = await link.require()
    params = {"sortKey": "updated_at", "limit": 50, "modelProviders": [], "currentAccountOnly": True}
    if query:
        params["searchTerm"] = query
    result = await server.request("thread/list", params)
    threads = {}
    for item in result["data"]:
        thread = ThreadSummary.model_validate(item)
        threads.setdefault(UUID(thread.id).hex, thread)

    projects = await list_codex_projects(server)
    memberships = await asyncio.to_thread(assign_chat_projects, projects, threads) if projects and threads else {}
    groups = {project.id: {"project": project.model_dump(), "chats": []} for project in projects or []}
    other = {"project": None, "chats": []}
    for identity, thread in threads.items():
        group = groups.get(memberships.get(identity), other)
        group["chats"].append({
            "identity": identity, "name": thread.name or external_name(identity), "workspace": thread.cwd,
        })
    visible = [group for group in groups.values() if group["chats"]]
    if other["chats"]:
        visible.append(other)
    return {"groups": visible, "projects_available": projects is not None}


def _message(item):
    if item["type"] == "userMessage":
        text = "\n".join(block["text"] for block in item["content"] if block["type"] == "text")
        role = "user"
    elif item["type"] == "functionCallOutput" and item.get("name") == A2A_CODEX_INPUT:
        text, role = item["output"], "delegation"
    elif item["type"] == "agentMessage" and item.get("phase") in (None, "final_answer"):
        text, role = item["text"], "codex"
    else:
        return None
    return {"id": item["id"], "role": role, "text": text} if text else None


async def _history(server, thread):
    result = await server.request("thread/turns/list", {
        "threadId": thread.id, "limit": 20, "sortDirection": "desc", "itemsView": "full",
    })
    turns = result["data"]
    messages = [
        message for turn in reversed(turns) for item in turn["items"]
        if (message := _message(item)) is not None
    ]
    if thread.cwd is None:
        raise ValueError("Codex did not report its working directory")
    return {
        "thread_id": thread.id, "workspace": thread.cwd, "messages": messages,
        "last_status": turns[0]["status"] if turns else None, "has_earlier": result["nextCursor"] is not None,
    }


async def read_codex_chat(link, identity: str):
    server = await link.require()
    return await _history(server, await native_codex_thread(server, identity))


async def send_codex_chat(link, identity: str, text: str, *, accept_first=None):
    server = await link.require()
    try:
        async with edit_codex_chat(server, identity=identity) as thread:
            if accept_first is not None:
                history = await _history(server, thread)
                if any(message["role"] in {"user", "delegation"} for message in history["messages"]):
                    await accept_first()
                    return history
            await server.run_turn(thread.id, {"input": [{"type": "text", "text": text}]},
                                  timeout=900, on_accepted=accept_first)
            return await _history(server, thread)
    except (CodexDisconnected, TimeoutError) as error:
        raise CodexChatUnavailable("未收到 Codex 的确认。请重新连接并查看原会话后，再决定是否重发。") from error
