"""User-owned Bot file access; execution environments consume a current snapshot."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from konden.assistant.host.agent_roster import AgentDeletedError, AgentNotFound
from konden.assistant.subagent.subagent import project_parent, project_pending
from konden.assistant.workspaces import bound_workspace
from konden.config import write_json
from konden.filesystem.workspace import FileWorkspace
from konden.assistant.sessions import session_view


class DirectoryAccess(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    mode: Literal["directories"] = "directories"
    directories: list[str] = Field(default_factory=list)


class FullAccess(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    mode: Literal["full"]


FileAccessConfig = Annotated[DirectoryAccess | FullAccess, Field(discriminator="mode")]
_CONFIG = TypeAdapter(FileAccessConfig)


class FileAccessInvalid(ValueError):
    pass


class FileAccessBusy(ValueError):
    pass


def normalize_access(config: FileAccessConfig) -> FileAccessConfig:
    if isinstance(config, FullAccess):
        return config
    directories = []
    for raw in config.directories:
        if not raw.strip() or "\0" in raw or not Path(raw).is_absolute():
            raise FileAccessInvalid("访问目录必须是本机的绝对路径。")
        try:
            path = Path(raw).resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError):
            raise FileAccessInvalid(f"目录不存在：{raw}") from None
        except PermissionError:
            raise FileAccessInvalid(f"无法访问目录：{raw}") from None
        if not path.is_dir():
            raise FileAccessInvalid(f"这不是目录：{raw}")
        if path not in directories:
            directories.append(path)
    return DirectoryAccess(directories=[str(path) for path in directories])


class BotFileAccess:
    def __init__(self, host):
        self.host = host

    def path(self, session_id):
        return self.host.store.require(session_id).parent / "file-access.json"

    def load(self, session_id) -> FileAccessConfig:
        path = self.path(session_id)
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return DirectoryAccess()
        return _CONFIG.validate_json(content)

    async def _agent(self, session_id):
        agent = await self.host.agents.by_session(session_id)
        if agent is None:
            raise AgentNotFound(session_id)
        if agent.deleted:
            raise AgentDeletedError(session_id)
        return agent

    async def get(self, session_id):
        await self._agent(session_id)
        return self.load(session_id)

    async def workspace(self, session_id, events):
        workspace = bound_workspace(session_id, events, self.host.workspaces)
        owner = session_id
        owner_events = events
        while True:
            reader = self.host.compact.store.reader_job(owner)
            parent = reader["source"] if reader is not None else project_parent(owner_events)
            if parent is None:
                break
            owner = parent
            owner_events = await self.host.store.journal(owner).snapshot(owner)
        if isinstance(workspace, FileWorkspace):
            config = self.load(owner)
            workspace = replace(workspace, full_access=config.mode == "full",
                                directories=tuple(Path(path) for path in config.directories)
                                if isinstance(config, DirectoryAccess) else ())
        return workspace

    async def save(self, session_id, config):
        config = normalize_access(config)
        # request() uses this same lock: no new input/delivery can acquire an old
        # environment between the idle handshake and the durable settings write.
        async with self.host.turn_locks.setdefault(session_id, asyncio.Lock()):
            async with self.host.locks.setdefault(session_id, asyncio.Lock()):
                await self._agent(session_id)
                events, state = await self.host.store.projected(session_id)
                if (state.waiting_command_ids or session_view(state).should_wake
                        or project_pending(events) or self.host.compact.store.job(session_id) is not None):
                    raise FileAccessBusy("请先停止当前任务并回收子任务，再修改文件访问范围。")
                if self.host.commands is not None and any(
                    command["state"] == "running"
                    for command in await self.host.commands.manager.list(session_id)
                ):
                    raise FileAccessBusy("请先停止后台命令，再修改文件访问范围。")
                worker = self.host.workers.get(session_id)
                if worker is not None and not worker.exited.is_set():
                    if worker.requests or worker.running:
                        raise FileAccessBusy("Bot 正在处理任务，请空闲后再保存。")
                    # A read-only page request invalidates the preceding idle
                    # revision too. Wait for its new advertisement, not a new task.
                    while worker.idle_revision is None and not worker.exited.is_set():
                        worker.changed.clear()
                        if worker.running:
                            raise FileAccessBusy("Bot 开始了新的任务，请空闲后再保存。")
                        await worker.changed.wait()
                    if not worker.exited.is_set() and not worker.stopping:
                        worker.stopping = True
                        worker.transition.clear()
                        await worker.peer.send(("stop", worker.idle_revision))
                    await worker.transition.wait()
                    if not worker.exited.is_set():
                        raise FileAccessBusy("Bot 开始了新的任务，请空闲后再保存。")
                    if worker.failure is not None:
                        raise worker.failure
                write_json(self.path(session_id), config.model_dump())
        return config
