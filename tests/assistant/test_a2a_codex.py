from __future__ import annotations

import asyncio
from functools import partial

from PIL import Image

from konden.assistant.a2a_bots import A2A_COMPLETED
from konden.assistant.host import a2a_codex
from konden.assistant.host.a2a_bots import A2ABots
from konden.paths import KondenHome


def test_a2a_files_belong_to_the_requesting_conversation(monkeypatch, tmp_path):
    thread_id = "12345678-abcd-7123-abcd-123456789abc"

    async def produce(name, workspace, work):
        assert name == "Codex 工程 Bot"
        assert work.brief == "交付图和数据"
        Image.new("RGB", (20, 10), color="blue").save(workspace / "chart.png")
        (workspace / "data.csv").write_bytes(b"day,count\nFri,10\n")
        return a2a_codex.CodexDelivery(thread_id, "已生成图和数据。")

    monkeypatch.setattr(a2a_codex, "run_a2a_task", partial(a2a_codex.run_a2a_task, execute=produce))
    home = KondenHome(tmp_path)
    sessions_root = tmp_path / "r"
    bots = A2ABots(home, sessions_root)
    codex = bots.create("Codex 工程 Bot")
    assert A2ABots(home, sessions_root).list() == [codex]

    result = asyncio.run(bots.delegate("coordinator", {
        "bot_id": codex.bot_id, "brief": "交付图和数据", "output_files": ["chart.png", "data.csv"],
    }))

    assert result["code"] == A2A_COMPLETED
    assert result["data"]["report"] == "已生成图和数据。"
    assert result["data"]["codex_thread_id"] == thread_id
    image, csv = result["data"]["files"]
    assert [image["name"], csv["name"]] == ["chart.png", "data.csv"]
    store = bots.attachments.for_session("coordinator")
    assert store.read(csv["attachment_id"]) == b"day,count\nFri,10\n"
    with Image.open(store.path(image["attachment_id"])) as preview:
        assert preview.size == (20, 10)
    assert not asyncio.run(bots.attachments.available_in_session("another", csv["attachment_id"]))


def test_a2a_task_without_a_declared_artifact_cannot_publish_a_partial_delivery(monkeypatch, tmp_path):
    async def produce(_name, workspace, _work):
        (workspace / "first.csv").write_bytes(b"1,2")
        return a2a_codex.CodexDelivery("00000000-0000-0000-0000-000000000001", "已完成。")

    monkeypatch.setattr(a2a_codex, "run_a2a_task", partial(a2a_codex.run_a2a_task, execute=produce))
    home = KondenHome(tmp_path)
    bots = A2ABots(home, home.runtime_sessions_root)
    codex = bots.create("Codex")

    result = asyncio.run(bots.delegate("coordinator", {
        "bot_id": codex.bot_id, "brief": "交付数据", "output_files": ["first.csv", "missing.csv"],
    }))

    assert result["code"] == "A2A_TASK_FAILED"
    assert "missing.csv" in result["error"]
    assert not bots.attachments.for_session("coordinator").files.originals.exists()
