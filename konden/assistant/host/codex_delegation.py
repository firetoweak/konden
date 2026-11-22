"""Delegate to Codex sessions and import completed A2A deliveries."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from io import BytesIO
from uuid import UUID, uuid4

from konden.assistant.a2a import A2A_COMPLETED, CodexWork
from konden.assistant.attachments import AttachmentGateway, AttachmentSnapshot
from konden.assistant.host.codex_app_server import CodexDisconnected


class CodexDelegation:
    def __init__(self, home, sessions_root, external):
        self.workspaces = home.state_root / "a2a_tasks"
        self.attachments = AttachmentGateway(sessions_root)
        self.external = external

    async def delegate(self, session_id, arguments):
        from konden.a2a_transport import ResultUnknown, WorkFailed
        from konden.assistant.host.a2a_codex import run_a2a_task

        work = CodexWork.model_validate(dict(arguments))
        if work.codex_session_id is not None and not self.external.has_local_codex(work.codex_session_id):
            return {"ok": False, "code": "CODEX_SESSION_NOT_FOUND", "error": "请选择已关联的 Codex 会话。"}
        try:
            server = await self.external.codex_link.require()
        except CodexDisconnected as error:
            return {"ok": False, "code": "CODEX_DISCONNECTED", "error": str(error)}
        workspace = None
        if work.codex_session_id is None:
            workspace = (self.workspaces / uuid4().hex).resolve()
            workspace.mkdir(parents=True)
        accepted_thread = None

        async def accepted(thread_id):
            nonlocal accepted_thread
            accepted_thread = str(UUID(thread_id))
            if work.codex_session_id is None:
                await self.external.bind_local_codex(UUID(thread_id).hex)

        try:
            task = await run_a2a_task(server, workspace, work, accepted)
        except WorkFailed as error:
            return {"ok": False, "code": "A2A_TASK_FAILED", "error": str(error),
                    **({"data": {"codex_thread_id": accepted_thread}} if accepted_thread else {})}
        except ResultUnknown as error:
            return {"ok": None, "code": "A2A_RESULT_UNKNOWN", "error": str(error), "data": {
                "task_id": error.task_id, "codex_thread_id": accepted_thread,
            }}
        finally:
            if accepted_thread is not None:
                await self.external.notify_changed()

        files, report, name = await asyncio.to_thread(self._receive_delivery, session_id, work, task, accepted_thread)
        return {"ok": True, "code": A2A_COMPLETED, "data": {
            "codex_session_name": name, "task_id": task.id, "report": report, "files": files,
            "codex_thread_id": accepted_thread,
        }}

    def _receive_delivery(self, session_id, work, task, accepted_thread):
        from konden.a2a_transport import MAX_ARTIFACT_BYTES
        from konden.assistant.host.a2a_codex import CODEX_SESSION_NAME, CODEX_THREAD_ID

        (receipt,) = (dict(artifact.metadata) for artifact in task.artifacts if CODEX_THREAD_ID in artifact.metadata)
        if str(UUID(receipt[CODEX_THREAD_ID])) != accepted_thread:
            raise ValueError("A2A returned a different Codex session")
        name = receipt.get(CODEX_SESSION_NAME, "Codex")
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
        return files, report, name
