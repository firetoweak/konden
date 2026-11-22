"""Persistent named A2A Bots and importing their completed deliveries."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from io import BytesIO
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from konden.assistant.a2a_bots import A2A_COMPLETED, CodexWork, DelegateA2AWork
from konden.assistant.attachments import AttachmentGateway, AttachmentSnapshot
from konden.assistant.host.codex_app_server import CodexDisconnected
from konden.config import write_json


class A2ABotNameTaken(ValueError):
    pass


class A2ABotName(BaseModel):
    name: str = Field(min_length=1, max_length=60)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if value != value.strip() or any(ord(character) < 32 for character in value):
            raise ValueError("Bot 名字不能有首尾空白或控制字符")
        return value


class A2ABot(A2ABotName):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    bot_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class A2ABotRegistry(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    bots: list[A2ABot]


class A2ABots:
    def __init__(self, home, sessions_root, external):
        self.path = home.state_root / "a2a_bots.json"
        self.workspaces = home.state_root / "a2a_tasks"
        self.attachments = AttachmentGateway(sessions_root)
        self.external = external

    def list(self):
        try:
            data = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        return A2ABotRegistry.model_validate_json(data).bots

    def create(self, name):
        # These operations are synchronous within the sole Host for this data root.
        bots = self.list()
        if any(bot.name == name for bot in bots):
            raise A2ABotNameTaken("已经有同名的 A2A Bot。")
        bot = A2ABot(bot_id=uuid4().hex, name=name)
        write_json(self.path, A2ABotRegistry(bots=[*bots, bot]).model_dump())
        return bot

    async def delegate(self, session_id, arguments):
        from konden.a2a_transport import ResultUnknown, WorkFailed
        from konden.assistant.host.a2a_codex import run_a2a_task

        work = DelegateA2AWork.model_validate(dict(arguments))
        bot = next((item for item in self.list() if item.bot_id == work.bot_id), None)
        if bot is None:
            return {"ok": False, "code": "A2A_BOT_NOT_FOUND", "error": "没有找到这位 A2A Bot，请重新查询名册。"}
        if work.codex_session_id is not None and not self.external.has_local_codex(work.codex_session_id):
            return {"ok": False, "code": "CODEX_SESSION_NOT_FOUND", "error": "请选择已关联的 Codex 会话。"}
        try:
            server = await self.external.codex_link.require()
        except CodexDisconnected as error:
            return {"ok": False, "code": "CODEX_DISCONNECTED", "error": str(error)}
        workspace = None
        if work.codex_session_id is None:
            workspace = (self.workspaces / bot.bot_id / uuid4().hex).resolve()
            workspace.mkdir(parents=True)
        native_work = CodexWork.model_validate(work.model_dump(exclude={"bot_id"}))
        try:
            task = await run_a2a_task(server, bot.name, workspace, native_work)
        except WorkFailed as error:
            return {"ok": False, "code": "A2A_TASK_FAILED", "error": str(error)}
        except ResultUnknown as error:
            return {"ok": None, "code": "A2A_RESULT_UNKNOWN", "error": str(error), "data": {"task_id": error.task_id}}

        files, report, thread_id = await asyncio.to_thread(self._receive_delivery, session_id, work, task)
        if work.codex_session_id is None:
            await self.external.bind_local_codex(UUID(thread_id).hex)
        else:
            await self.external.notify_changed()
        return {"ok": True, "code": A2A_COMPLETED, "data": {
            "bot_name": bot.name, "task_id": task.id, "report": report, "files": files, "codex_thread_id": thread_id,
        }}

    def _receive_delivery(self, session_id, work, task):
        from konden.a2a_transport import MAX_ARTIFACT_BYTES
        from konden.assistant.host.a2a_codex import CODEX_THREAD_ID

        (native_id,) = (artifact.metadata[CODEX_THREAD_ID] for artifact in task.artifacts
                        if CODEX_THREAD_ID in artifact.metadata)
        thread_id = str(UUID(native_id))
        if work.codex_session_id is not None and UUID(thread_id).hex != work.codex_session_id:
            raise ValueError("A2A returned a different Codex session")
        parts = [part for artifact in task.artifacts for part in artifact.parts]
        incoming = [part for part in parts if part.HasField("raw")]
        if sorted(part.filename for part in incoming) != sorted(work.output_files):
            raise ValueError("A2A completed task returned a different set of files")
        if sum(len(part.raw) for part in incoming) > MAX_ARTIFACT_BYTES:
            raise ValueError("A2A completed task exceeded the file limit")
        report = "\n\n".join(part.text for part in parts if part.HasField("text"))
        store = self.attachments.for_session(session_id)
        snapshot = AttachmentSnapshot()
        try:
            saved = [store.save_file_stream(BytesIO(part.raw), part.filename, snapshot=snapshot) for part in incoming]
        except BaseException as error:
            try:
                snapshot.discard()
            except OSError as cleanup_error:
                raise BaseExceptionGroup("A2A attachment write and cleanup failed", [error, cleanup_error]) from None
            raise
        snapshot.publish()
        files = [asdict(file) | {"path": str(store.files.materialize(file.attachment_id))} for file in saved]
        return files, report, thread_id
