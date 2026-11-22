"""Shared attachments must survive retries and stay inside the addressed conversation."""
import asyncio
from base64 import b64decode
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
import pytest

from konden.assistant.attachments import AttachmentGateway
from konden.assistant.host.agent_roster import AgentDeletedError
from konden.assistant.host.codex_titles import ThreadSummary
from konden.assistant.host.external_messages import ExternalMessages
from konden.assistant.host.supervisor import HostSupervisor, Worker
from konden.runtime import StateProjector
from tests.channels.test_collaboration import collaboration as collaboration
from tests.fixtures.projection import project_chat_messages


def png():
    out = BytesIO()
    Image.new("RGB", (32, 24), "red").save(out, format="PNG")
    return out.getvalue()


def upload(c, name, data, mime):
    response = c.client.post("/api/sessions/coordinator/attachments",
        data={"connection_id": c.human_connection}, files={"file": (name, data, mime)})
    assert response.status_code == 201, response.text
    return response.json()["attachment_id"]


def test_web_shares_images_and_ordinary_files_with_scoped_mcp_read_and_download(collaboration):
    """A shared token must not expose private files or another native chat's attachments."""
    c = collaboration
    data = b"%PDF-1.4\nattachment bytes\n"
    image = upload(c, "screen.png", png(), "image/png")
    file = upload(c, "中文 报告.pdf", data, "application/pdf")
    private = upload(c, "private.txt", b"PRIVATE", "text/plain")
    posted = c.post(text="[Image #1] [File #1]", target="both", artifact_refs=[image, file])
    assert posted.status_code == 200, posted.text
    message = posted.json()["messages"][0]
    assert [a["attachment_id"] for a in message["attachments"]] == [image, file]
    assert c.user_inputs[0][2]["artifact_refs"] == (image, file)
    incoming = c.read()["messages"][0]
    assert incoming["attachments"] == message["attachments"]
    def read(ref, **extra):
        return c.call("read_attachment", {"message_id": message["message_id"], "attachment_id": ref}, **extra)
    assert b64decode(read(image)["content"][1]["data"]) == png()
    ordinary = read(file)["structuredContent"]
    assert Path(ordinary["path"]).read_bytes() == data
    assert ordinary["attachment"]["name"] == "中文 报告.pdf"
    c.call("get_context", chat="2" * 32)
    assert read(file, chat="2" * 32)["structuredContent"]["code"] == "ATTACHMENT_NOT_SHARED"
    assert read(private)["structuredContent"]["code"] == "ATTACHMENT_NOT_SHARED"
    assert c.call("read_attachment", {"message_id": "other", "attachment_id": file})["structuredContent"]["code"] == "ATTACHMENT_NOT_SHARED"
    download = c.client.get(f"/api/sessions/coordinator/attachments/{file}")
    assert download.content == data and "filename" in download.headers["content-disposition"]
    assert c.post(delivery_id="foreign", bot_session_id="other", artifact_refs=[file]).status_code == 400
    restored = c.client.portal.call(ExternalMessages(c.host).read_attachment, c.connection, c.identity, message["message_id"], file)
    assert Path(restored["path"]).read_bytes() == data
    c.client.portal.call(c.service.revoke_connection, c.connection.connection_id)
    assert c.client.portal.call(c.service.read_attachment, c.connection, c.identity, message["message_id"], file)["code"] == "CONNECTION_REVOKED"


def test_codex_snapshots_workspace_files_once_and_bot_observes_their_materials(collaboration, tmp_path):
    """Retrying a delivered file must neither reread a changed source nor deliver new bytes."""
    c = collaboration
    source = tmp_path / "codex"
    source.mkdir()
    (source / "chart.png").write_bytes(png())
    (source / "data.zip").write_bytes(b"PK\x03\x04original zip bytes")
    native = {c.identity: ThreadSummary(id="11111111-1111-1111-1111-111111111111", cwd=str(source))}
    with patch("konden.assistant.host.external_messages.read_codex_threads", new=AsyncMock(return_value=native)) as metadata:
        sent = c.send(files=["chart.png", "data.zip"])
        assert sent["code"] == "DELIVERED"
        (source / "data.zip").write_bytes(b"changed")
        assert c.send(files=["chart.png", "data.zip"]) == sent
        assert metadata.await_count == 1
        assert c.send(files=["data.zip"])["code"] == "DELIVERY_CONFLICT"
        assert c.send(delivery_id="escape", files=["../private.txt"])["code"] == "ATTACHMENT_REJECTED"
        assert c.send(delivery_id="absolute", files=[str(source / "data.zip")])["code"] == "ATTACHMENT_REJECTED"
    messages = c.client.get(f"/api/external-sessions/{c.identity}").json()["messages"]
    assert len(messages) == 1
    ref = messages[0]["attachments"][1]["attachment_id"]
    assert AttachmentGateway(c.store.root).for_session("coordinator").read(ref) == b"PK\x03\x04original zip bytes"

    async def model_view():
        events = await c.store.journal("coordinator").snapshot("coordinator")
        state = StateProjector().project_visible("coordinator", events)
        return project_chat_messages(events, state, attachments=AttachmentGateway(c.store.root).for_session("coordinator"))

    shown = c.client.portal.call(model_view)
    external = next(m for m in shown if isinstance(m["content"], list))
    assert len(external["content"]) == 2 and external["content"][1]["type"] == "image"
    text = external["content"][0]["text"]
    assert "data.zip" in text and "三方消息附带" in text
    assert '"kind": "external"' in text and "attachment_paths" not in text
    with patch("konden.assistant.host.external_messages.read_codex_threads", new=AsyncMock(return_value={})):
        assert c.send(delivery_id="unknown-root", files=["data.zip"])["code"] == "FILE_CONTEXT_UNAVAILABLE"


def test_bot_file_reply_is_shared_once_with_a_stable_snapshot(collaboration, tmp_path):
    c = collaboration
    (tmp_path / "report.xlsx").write_bytes(b"PK\x03\x04spreadsheet bytes")
    sent = c.reply(files=["report.xlsx"])
    assert sent["code"] == "DELIVERED"
    (tmp_path / "report.xlsx").unlink()
    assert c.reply(files=["report.xlsx"]) == sent
    incoming = c.read()["messages"][0]
    ref = incoming["attachments"][0]["attachment_id"]
    result = c.call("read_attachment", {"message_id": incoming["message_id"], "attachment_id": ref})["structuredContent"]
    assert Path(result["path"]).read_bytes() == b"PK\x03\x04spreadsheet bytes"
    assert c.reply(command="outside", files=["../secrets.txt"])["code"] == "ATTACHMENT_REJECTED"
    assert len(c.client.get(f"/api/external-sessions/{c.identity}").json()["messages"]) == 1


def test_failed_share_discards_new_originals_before_retry(collaboration, tmp_path):
    c = collaboration
    store = AttachmentGateway(c.store.root).for_session("coordinator")
    (tmp_path / "report.txt").write_bytes(b"original")
    (tmp_path / "chart.png").write_bytes(png())
    files = ["report.txt", "chart.png"]
    with patch.object(c.host, "request", side_effect=OSError("before commit")), pytest.raises(OSError, match="before commit"):
        c.reply(files=files)
    assert c.client.portal.call(c.service.messages, "coordinator") == []
    drawer = store.files.originals.parent
    assert not [p for p in drawer.rglob("*") if p.is_file()]
    assert list(store.files.originals.iterdir()) == []

    sent = c.reply(files=files)
    assert sent["code"] == "DELIVERED"
    assert c.reply(files=files) == sent
    message, = c.read()["messages"]
    assert [store.read(a["attachment_id"]) for a in message["attachments"]] == [b"original", png()]
    assert len([p for p in drawer.rglob("*") if p.is_file()]) == 2


def test_rejected_batch_preserves_previously_shared_image(collaboration, tmp_path):
    c = collaboration
    store = AttachmentGateway(c.store.root).for_session("coordinator")
    shared = upload(c, "chart.png", png(), "image/png")
    assert c.post(artifact_refs=[shared]).status_code == 200
    (tmp_path / "chart.png").write_bytes(png())
    (tmp_path / "report.txt").write_bytes(b"private")
    (tmp_path / "broken.png").write_bytes(b"not an image")
    assert c.reply(files=["chart.png", "report.txt", "broken.png"])["code"] == "ATTACHMENT_REJECTED"
    message, = c.read()["messages"]
    assert message["attachments"][0]["attachment_id"] == shared
    assert store.read(shared) == png()
    assert list(store.files.originals.iterdir()) == []


def test_partial_copy_reports_storage_error_and_discards_batch(collaboration, tmp_path):
    c = collaboration
    store = AttachmentGateway(c.store.root).for_session("coordinator")
    (tmp_path / "chart.png").write_bytes(png())
    (tmp_path / "report.txt").write_bytes(b"original")

    def failed_copy(_source, output, **options):
        output.write(b"partial")
        raise PermissionError(13, "snapshot write denied", output.name)

    with patch("konden.assistant.file_attachments.shutil.copyfileobj", failed_copy), \
            pytest.raises(PermissionError, match="snapshot write denied"):
        c.reply(files=["chart.png", "report.txt"])
    assert c.read()["messages"] == []
    assert not [p for p in store.files.originals.parent.rglob("*") if p.is_file()]
    assert list(store.files.originals.iterdir()) == []


def test_deleted_recipient_keeps_image_adopted_by_another_upload(collaboration, tmp_path):
    c = collaboration
    store = AttachmentGateway(c.store.root).for_session("coordinator")
    (tmp_path / "chart.png").write_bytes(png())
    (tmp_path / "report.txt").write_bytes(b"original")
    files = ["chart.png", "report.txt"]
    native = {c.identity: ThreadSummary(id="11111111-1111-1111-1111-111111111111", cwd=str(tmp_path))}

    async def deleted(operation, session_id, arguments, **options):
        assert store.save_image(png(), "image/png").attachment_id == arguments["artifact_refs"][0]
        raise AgentDeletedError(session_id)

    with patch("konden.assistant.host.external_messages.read_codex_threads", new=AsyncMock(return_value=native)):
        with patch.object(c.host, "request", side_effect=deleted):
            assert c.send(files=files)["code"] == "RECIPIENT_NOT_FOUND"
        assert c.client.portal.call(c.service.messages, "coordinator") == []
        assert [p.read_bytes() for p in store.files.originals.parent.rglob("*") if p.is_file()] == [png()]
        assert c.send(files=files)["code"] == "DELIVERED"
        assert c.send(files=files)["code"] == "DELIVERED"
    (_, message), = c.client.portal.call(c.service.messages, "coordinator")
    assert [store.read(ref) for ref in message.artifact_refs] == [png(), b"original"]
    assert len([p for p in store.files.originals.parent.rglob("*") if p.is_file()]) == 2


@pytest.mark.parametrize("lost", ["ack", "notification"])
def test_failure_after_commit_preserves_readable_attachments(collaboration, tmp_path, lost):
    c = collaboration
    store = AttachmentGateway(c.store.root).for_session("coordinator")
    (tmp_path / "chart.png").write_bytes(png())
    (tmp_path / "report.txt").write_bytes(b"original")
    files = ["chart.png", "report.txt"]
    deliver = c.host.request.side_effect

    async def lost_ack(operation, session_id, arguments, **options):
        await deliver(operation, session_id, arguments)
        # A Worker can project the committed image before the Host gets its reply.
        assert store.read(arguments["artifact_refs"][0]) == png()
        raise OSError("lost ack")

    failure = (patch.object(c.host, "request", side_effect=lost_ack) if lost == "ack"
               else patch.object(c.service, "changed_sink", AsyncMock(side_effect=OSError("lost notification"))))
    with failure, pytest.raises(OSError, match="lost"):
        c.reply(files=files)
    (tmp_path / "report.txt").unlink()
    assert c.reply(files=files)["code"] == "DELIVERED"
    message, = c.read()["messages"]
    assert [store.read(a["attachment_id"]) for a in message["attachments"]] == [png(), b"original"]


def test_failed_cleanup_keeps_delivery_error(collaboration, tmp_path):
    c = collaboration
    (tmp_path / "report.txt").write_bytes(b"original")
    delivery = OSError("before commit")
    cleanup = PermissionError("original still open")
    with patch.object(c.host, "request", side_effect=delivery), \
            patch("konden.assistant.file_attachments.shutil.rmtree", side_effect=cleanup), \
            pytest.raises(ExceptionGroup) as failed:
        c.reply(files=["report.txt"])
    assert failed.value.exceptions == (delivery, cleanup)


@pytest.mark.parametrize("dispatched", [False, True], ids=["before-dispatch", "in-flight"])
def test_cancelled_share_keeps_only_accepted_attachments(collaboration, tmp_path, dispatched):
    c = collaboration
    (tmp_path / "report.txt").write_bytes(b"original")
    deliver = c.host.request.side_effect

    async def cancel_during_commit():
        started, release = asyncio.Event(), asyncio.Event()

        async def hold():
            started.set()
            await release.wait()

        async def exchange(operation, session_id, arguments):
            if operation == "fact":
                await hold()
                await deliver(operation, session_id, arguments)

        async def start(session_id):
            if not dispatched:
                await hold()
            return Worker(process=None, peer=SimpleNamespace(request=exchange))

        with patch.object(c.host, "request", HostSupervisor.request.__get__(c.host)), \
                patch.object(c.host, "_start", side_effect=start):
            task = asyncio.create_task(c.service.send_from_bot("coordinator", {
                "identity": c.identity, "text": "发送", "reply_to": None, "target": None,
                "command_id": "cancelled", "files": ["report.txt"]}))
            await asyncio.wait_for(started.wait(), 10)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 10)

    c.client.portal.call(cancel_during_commit)
    store = AttachmentGateway(c.store.root).for_session("coordinator")
    messages = c.read()["messages"]
    if dispatched:
        message, = messages
        assert store.read(message["attachments"][0]["attachment_id"]) == b"original"
    else:
        assert messages == []
        assert list(store.files.originals.iterdir()) == []
