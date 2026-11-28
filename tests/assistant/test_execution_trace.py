from __future__ import annotations

import asyncio
import json

import pytest

from konden.assistant.agents import AgentCreated, fact_arguments
from konden.assistant.artifacts import FileArtifactGateway
from konden.assistant.execution_trace import ExecutionTraceQueries
from konden.assistant.host.session_store import SessionStore
from konden.assistant.runner import SessionNotFoundError
from konden.assistant.subagent.subagent import (
    project_delegate_intents,
    task_fact_arguments,
)
from konden.runtime import (
    AgentRuntime,
    InvokeTool,
    ModelDecision,
    RecordedDecision,
    ToolBinding,
)
from tests.assistant.test_runner import ScriptedDecisionMaker, SequentialIds


async def make_bot(store, session_id, directory):
    await store.create(session_id, directory=directory, initial_fact=fact_arguments(
        AgentCreated(name=session_id, directory=str(directory)), delivery_id="created",
    ))


def test_trace_preserves_canonical_failure_and_unknown_and_loads_request_on_selection(tmp_path):
    async def exercise():
        session_id = "trace-bot"
        store = SessionStore(tmp_path / "sessions")
        await make_bot(store, session_id, tmp_path)
        image = {"type": "image", "id": "sha256:" + "a" * 64, "width": 32, "height": 16}
        request = {"model": "recorded/model", "tools": None, "messages": [
            {"role": "system", "content": "front matter"},
            {"role": "user", "content": [{"type": "text", "text": "analyze"}, image]},
            {"role": "system", "content": "later system message"},
        ]}
        artifact = FileArtifactGateway(store.root).for_session(session_id).save(json.dumps({
            "schema": "decision-replay-manifest/v2", "request": request,
        }))
        release = asyncio.Event()

        async def fail(_context, _arguments):
            return {"ok": False, "code": "REPORTED_FAILURE", "error": "observable failure"}

        async def wait(_context, _arguments):
            await release.wait()
            return {"ok": True}

        long_argument = "path/" * 60
        runtime = AgentRuntime(store.journal(session_id), ScriptedDecisionMaker((lambda _: RecordedDecision(
            ModelDecision("Record the outcome.", (InvokeTool("read", (("path", long_argument),)), InvokeTool("wait"))),
            (artifact.artifact_id,), {"model_usage": {"input_tokens": 100, "cached_input_tokens": 75}},
        ),)), {"read": ToolBinding(fail), "wait": ToolBinding(wait)}, SequentialIds())
        await runtime.receive_user_message(session_id, "analyze", delivery_id="message", artifact_refs=(image["id"],))
        await runtime.advance(session_id)
        try:
            async with asyncio.timeout(5):
                while runtime.dispatcher.active_count > 1:
                    await asyncio.sleep(0.001)
            trace = ExecutionTraceQueries(store)
            index = await trace.index(session_id, [])
            calls = {entry["title"]: entry for entry in index["entries"] if entry["kind"] == "tool"}
            assert {name: entry["status"] for name, entry in calls.items()} == {"read": "failed", "wait": "unknown"}
            assert calls["read"]["result"] == "observable failure"
            assert long_argument not in calls["read"]["preview"]
            assert calls["wait"]["end"] is None
            detail = await trace.detail(session_id, [], calls["read"]["id"])
            assert detail["arguments"] == {"path": long_argument}
            assert detail["outcome"]["value"]["ok"] is False
            unknown = await trace.detail(session_id, [], calls["wait"]["id"])
            assert unknown["outcome"] is None
            assert unknown["dispatched_at"] is not None and unknown["received_at"] is None
            request_entry = next(entry for entry in index["entries"] if entry["kind"] == "input")
            assert request_entry["user"]
            assert "request" not in request_entry
            assert (await trace.detail(session_id, [], request_entry["id"]))["request"] == request
            model = next(entry for entry in index["entries"] if entry["kind"] == "model")
            assert model["usage"] == {"input_tokens": 100, "cached_input_tokens": 75}
        finally:
            release.set()
            async with asyncio.timeout(5):
                while runtime.dispatcher.active_count:
                    await asyncio.sleep(0.001)

    asyncio.run(exercise())


@pytest.mark.parametrize(("saved", "visible"), [
    ({"cached_input_tokens": 0}, {}),
    ({"cached_input_tokens": 75}, {"cached_input_tokens": 75}),
    ({"cache_read_input_tokens": None}, {}),
    ({"cache_read_input_tokens": 0, "cache_write_input_tokens": 80},
     {"cached_input_tokens": 0, "cache_write_input_tokens": 80}),
])
def test_trace_distinguishes_reported_cache_miss_from_missing_statistics(tmp_path, saved, visible):
    async def exercise():
        session_id = "trace-bot"
        store = SessionStore(tmp_path / "sessions")
        await make_bot(store, session_id, tmp_path)
        usage = {"input_tokens": 100, "output_tokens": 2}
        runtime = AgentRuntime(store.journal(session_id), ScriptedDecisionMaker((
            lambda _: RecordedDecision(ModelDecision("OK"), (), {"model_usage": {**usage, **saved}}),
        )), {}, SequentialIds())
        await runtime.receive_user_message(session_id, "Read.", delivery_id="message")
        await runtime.advance(session_id)
        index = await ExecutionTraceQueries(store).index(session_id, [])
        model = next(entry for entry in index["entries"] if entry["kind"] == "model")
        assert model["usage"] == {**usage, **visible}

    asyncio.run(exercise())


@pytest.mark.parametrize("recorded", [True, False])
def test_model_trace_shows_content_and_complete_tool_calls(tmp_path, recorded):
    async def exercise():
        session_id = "trace-bot"
        store = SessionStore(tmp_path / "sessions")
        await make_bot(store, session_id, tmp_path)
        response = {
            "content": "先查看原来的配置。",
            "calls": [{"id": "call-1", "name": "read_file", "arguments": '{"path": "config.json"}'}],
        }
        refs = ()
        if recorded:
            artifact = FileArtifactGateway(store.root).for_session(session_id).save(json.dumps({
                "schema": "decision-replay-manifest/v2",
                "request": {"model": "recorded/model", "tools": None, "messages": []},
                "response": {**response, "message_extensions": {}},
            }))
            refs = (artifact.artifact_id,)

        async def read_file(_context, _arguments):
            return {"ok": True}

        decision = ModelDecision("核对当前配置。", (InvokeTool("read_file", (("path", "approved.json"),)),))
        runtime = AgentRuntime(store.journal(session_id), ScriptedDecisionMaker((
            lambda _: RecordedDecision(decision, refs),
        )), {"read_file": ToolBinding(read_file)}, SequentialIds())
        await runtime.receive_user_message(session_id, "检查配置", delivery_id="message")
        await runtime.advance(session_id)
        await runtime.dispatcher.close()
        trace = ExecutionTraceQueries(store)
        index = await trace.index(session_id, [])
        node = next(entry for entry in index["entries"] if entry["kind"] == "model")
        detail = await trace.detail(session_id, [], node["id"])

        assert detail["output_source"] == ("recorded" if recorded else "committed")
        assert detail["output"] == (response if recorded else {
            "content": "核对当前配置。",
            "calls": [{"name": "read_file", "arguments": {"path": "approved.json"}}],
        })

    asyncio.run(exercise())


def test_subagent_trace_follows_the_committed_parent_command(tmp_path):
    async def exercise():
        parent, other = "parent", "other"
        store = SessionStore(tmp_path / "sessions")
        await make_bot(store, parent, tmp_path)
        await make_bot(store, other, tmp_path)

        async def delegate(_context, _arguments):
            return {"ok": True}

        runtime = AgentRuntime(store.journal(parent), ScriptedDecisionMaker((lambda _: ModelDecision(
            command_requests=(InvokeTool("delegate", (("task", "check the result"),)),),
        ),)), {"delegate": ToolBinding(delegate)}, SequentialIds())
        await runtime.advance(parent)
        async with asyncio.timeout(5):
            while runtime.dispatcher.active_count:
                await asyncio.sleep(0.001)
        intent, = project_delegate_intents(await store.journal(parent).snapshot(parent))
        trace = ExecutionTraceQueries(store)
        pending = await trace.index(parent, [intent.command_id])
        assert pending["task"] == "check the result" and pending["entries"] == []
        await store.create(intent.child_session_id, directory=tmp_path, initial_fact=task_fact_arguments(intent))
        child_runtime = AgentRuntime(store.journal(intent.child_session_id), ScriptedDecisionMaker((
            lambda _: ModelDecision("Child result."),
        )), {}, SequentialIds())
        await child_runtime.advance(intent.child_session_id)
        branch = await trace.index(parent, [intent.command_id])
        assert branch["session_id"] == intent.child_session_id
        assert any(entry.get("preview") == "Child result." for entry in branch["entries"])
        with pytest.raises(SessionNotFoundError):
            await trace.index(other, [intent.command_id])
        with pytest.raises(SessionNotFoundError):
            await trace.index(intent.child_session_id, [])

    asyncio.run(exercise())
