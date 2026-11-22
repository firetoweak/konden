"""Receive a Codex A2A delegation through the Konden Bot's normal conversation."""

from __future__ import annotations

import asyncio
from mimetypes import guess_type
from pathlib import Path

from a2a.types import Part

from konden.a2a_transport import (
    MAX_ARTIFACT_BYTES,
    ResultUnknown,
    TaskDelivery,
    WorkFailed,
    run_loopback_task,
)
from konden.assistant.attachments import AttachmentGateway, is_file_attachment_id
from konden.assistant.external_messages import SendExternal


def _parts(store, message, requested):
    names = [Path(relative).name for relative in message.attachment_paths]
    if requested and sorted(names) != sorted(requested):
        raise WorkFailed("Konden Bot 的交付附件与约定的文件名不一致。可在原对话查看实际交付。")
    if len(names) > 4:
        raise WorkFailed("本次 A2A 最多交付四个文件。")
    remaining = MAX_ARTIFACT_BYTES
    parts = []
    for name, ref in zip(names, message.artifact_refs, strict=True):
        if is_file_attachment_id(ref):
            path = store.files.materialize(ref)
            mime = guess_type(name)[0] or "application/octet-stream"
        else:
            path = store.path(ref)
            mime = store.inspect(ref).mime
        with path.open("rb") as stream:
            payload = stream.read(remaining + 1)
        remaining -= len(payload)
        if remaining < 0:
            raise WorkFailed("本次 A2A 交付的文件合计超过 8 MiB。")
        part = Part(raw=payload, filename=name, media_type=mime)
        part.metadata.update({"attachment_id": ref})
        parts.append(part)
    return parts


async def delegate_to_konden(service, connection, identity, work):
    receipt_id = None
    previous = None

    async def execute(task_id, request):
        nonlocal receipt_id, previous
        brief = request.brief
        if request.output_files:
            brief += "\n\n请交付这些文件：" + "、".join(request.output_files)
        accepted = await service.send(connection, identity, SendExternal(
            recipient=request.recipient, delivery_id=request.delivery_id, text=brief, target="bot",
        ), a2a_task_id=task_id)
        if not accepted["ok"]:
            if accepted["code"] == "A2A_TASK_ALREADY_SENT":
                previous = accepted
            raise WorkFailed(accepted["error"])
        receipt_id = accepted["message_id"]
        reply = await service.await_a2a_reply(connection, identity, receipt_id)
        if reply is None:
            raise ResultUnknown(task_id)
        session_id, message = reply
        if message.task_result == "failed":
            raise WorkFailed(message.text)
        store = AttachmentGateway(service.host.store.root).for_session(session_id)
        parts = await asyncio.to_thread(_parts, store, message, request.output_files)
        return TaskDelivery(message.text, parts, {"message_id": message.message_id})

    try:
        task = await run_loopback_task(work.recipient, work, execute)
    except WorkFailed as error:
        if previous is not None:
            return {**previous, "ok": None}, []
        return {"ok": False, "code": "A2A_TASK_FAILED", "error": str(error)}, []
    except ResultUnknown as error:
        return {"ok": None, "code": "A2A_RESULT_UNKNOWN", "error": str(error), "task_id": error.task_id,
                "message_id": receipt_id}, []
    (message_id,) = (artifact.metadata["message_id"] for artifact in task.artifacts if "message_id" in artifact.metadata)
    parts = [part for artifact in task.artifacts for part in artifact.parts]
    files = [part for part in parts if part.HasField("raw")]
    return {
        "ok": True, "code": "A2A_TASK_COMPLETED", "task_id": task.id, "message_id": message_id,
        "report": "\n\n".join(part.text for part in parts if part.HasField("text")),
        "attachments": [{"name": part.filename, "size": len(part.raw), "attachment_id": part.metadata["attachment_id"]}
                        for part in files],
    }, [(part.media_type, part.raw) for part in files if not is_file_attachment_id(part.metadata["attachment_id"])]
