"""Automatic memory checks must produce durable, scoped effects or an honest result."""
from __future__ import annotations

import asyncio
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from konden.assistant.agents import AgentCreated, fact_arguments
from konden.assistant.context.memory import (
    PERSONAL_MEMORY,
    SHARED_MEMORY,
    MemoryContext,
    memory_observations,
)
from konden.assistant.memory_review import REVIEW_FACT, MemoryReview
from konden.assistant.personal_memory import (
    UPDATE_PERSONAL_MEMORY,
    PersonalMemoryStore,
    personal_memory_bindings,
)
from konden.assistant.shared_memory import (
    UPDATE_SHARED_MEMORY,
    SharedMemoryStore,
    UpdateSharedMemory,
    shared_memory_bindings,
)
from konden.llm.api import (
    LLMCallResult,
    LLMProviderError,
    LLMResponse,
    LLMUsage,
    ToolCall,
)
from konden.runtime import (
    AgentRuntime,
    DomainFactCommitted,
    ModelDecision,
    RuntimeStatus,
)
from konden.runtime.json_values import thaw_value
from tests.fixtures.artifacts import MemoryArtifactGateway
from tests.fixtures.memory_journal import MemoryJournal
from tests.session_scheduler import settle_session


class ForegroundModel:
    async def decide(self, _frame):
        return ModelDecision(content="已处理。")


class ReviewModel:
    def __init__(self):
        self.requests = []
        self.response = LLMResponse(content="没有需要更新的长期约定。")
        self.during_call = None

    async def chat(self, messages, model, **options):
        self.requests.append(deepcopy({"messages": messages, "model": model, **options}))
        if self.during_call is not None:
            await self.during_call()
        return LLMCallResult(self.response, LLMUsage(input_tokens=20, output_tokens=10))


class MemoryReviewTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.session_id = "ada"
        self.journal = MemoryJournal()
        self.runtime = AgentRuntime(self.journal, ForegroundModel(), {})
        await self.runtime.create_session(self.session_id)
        await self.runtime.receive_domain_fact(
            self.session_id,
            **fact_arguments(AgentCreated(name="Ada", directory=str(self.root)), delivery_id="identity"),
        )
        self.shared = SharedMemoryStore(self.root / "shared.sqlite")
        self.personal = PersonalMemoryStore(self.root / "personal.sqlite")
        self.memory = MemoryContext(self.shared, self.personal)
        self.bindings = {
            **shared_memory_bindings(self.shared, "Ada"),
            **personal_memory_bindings(self.personal, "Ada"),
        }
        self.llm = ReviewModel()
        self.input_number = 0

    def review(self):
        return MemoryReview(
            runtime=self.runtime, session_id=self.session_id, bot_name="Ada", llm=self.llm,
            model=lambda: "test-model", reasoning_effort=lambda: None,
            memory=self.memory, bindings=self.bindings, gateway=MemoryArtifactGateway(),
        )

    async def turn(self, text=None):
        self.input_number += 1
        await self.runtime.receive_user_message(
            self.session_id, text or f"第 {self.input_number} 次普通交流。",
            delivery_id=f"user-{self.input_number}",
        )
        await settle_session(self.runtime, self.session_id)
        self.assertEqual((await self.runtime.state(self.session_id)).status, RuntimeStatus.WAITING)

    async def facts(self):
        return [thaw_value(event.payload.data)
                for event in await self.runtime.snapshot(self.session_id)
                if isinstance(event.payload, DomainFactCommitted) and event.payload.fact_type == REVIEW_FACT]

    async def finished(self):
        return [fact for fact in await self.facts() if fact["phase"] == "finished"]

    async def make_due(self, review):
        await review.initialize()
        for _ in range(5):
            await self.turn()

    def write_response(self, *, shared=False):
        name = UPDATE_SHARED_MEMORY if shared else UPDATE_PERSONAL_MEMORY
        content = "所有同事都用中文。" if shared else "代码审查先列阻断问题。"
        return LLMResponse(calls=(ToolCall("remember", name, json.dumps({
            "version": 0, "content": content,
        }, ensure_ascii=False)),))

    async def test_fifth_exchange_writes_existing_memory_and_next_observation_sees_it(self):
        # Prevent a review that merely claims to save, changes another scope, or never reaches context.
        review = self.review()
        await review.initialize()
        for _ in range(4):
            await self.turn()
        await self.turn("以后你做代码审查，先告诉我阻断问题。")
        self.llm.response = self.write_response()
        await review.run_if_due()

        self.assertEqual(self.personal.read().content, "代码审查先列阻断问题。")
        self.assertEqual(self.personal.read().updated_by, "Ada")
        self.assertEqual(self.shared.read().version, 0)
        result, = await self.finished()
        self.assertEqual(result["status"], "updated")
        self.assertIn("PERSONAL_MEMORY_UPDATED", json.dumps(result))
        with closing(sqlite3.connect(self.personal.path)) as db:
            source, operation = db.execute("SELECT source, operation_id FROM memory").fetchone()
        self.assertEqual(source, "bot:ada")
        self.assertTrue(operation)
        self.assertIn(result["review_id"], operation)
        self.assertEqual(len(self.llm.requests), 1)
        tools = self.llm.requests[0]["tools"]
        self.assertEqual({tool["function"]["name"] for tool in tools},
                         {UPDATE_SHARED_MEMORY, UPDATE_PERSONAL_MEMORY})
        await self.memory.observe(self.runtime, self.session_id)
        observed = memory_observations(await self.runtime.snapshot(self.session_id))
        self.assertEqual(observed[PERSONAL_MEMORY], self.personal.read().model_dump())
        self.assertEqual(observed[SHARED_MEMORY], self.shared.read().model_dump())

    async def test_unchanged_review_waits_for_five_exchanges_and_does_not_review_itself(self):
        # Empty reviews and passive memory observations must not cause a self-sustaining loop.
        review = self.review()
        await review.initialize()
        for _ in range(4):
            await self.turn()
            await review.run_if_due()
        self.assertEqual(self.llm.requests, [])
        await self.turn()
        await review.run_if_due()
        self.assertEqual((await self.finished())[-1]["status"], "unchanged")
        await self.memory.observe(self.runtime, self.session_id)
        await review.run_if_due()
        await review.run_if_due()
        self.assertEqual(len(self.llm.requests), 1)
        self.assertEqual(self.personal.read().version, 0)
        self.assertEqual(self.shared.read().version, 0)

    async def test_initialization_skips_old_completed_history_but_keeps_unprocessed_input(self):
        # Installing the feature must not mine all old chats or discard accepted, unfinished input.
        for _ in range(5):
            await self.turn("旧记录只用于基线，不应重新处理。")
        await self.runtime.receive_user_message(
            self.session_id, "已接收但尚未处理的新要求。", delivery_id="pending-input",
        )
        review = self.review()
        await review.initialize()
        await settle_session(self.runtime, self.session_id)
        for _ in range(4):
            await self.turn()
        await review.run_if_due()
        self.assertEqual(len(self.llm.requests), 1)
        shown = json.dumps(self.llm.requests[0]["messages"], ensure_ascii=False)
        self.assertIn("已接收但尚未处理的新要求", shown)
        self.assertNotIn("旧记录只用于基线", shown)

    async def test_provider_failure_is_recorded_without_claiming_memory_was_saved(self):
        review = self.review()
        await self.make_due(review)

        async def fail():
            raise LLMProviderError("provider rejected review")

        self.llm.during_call = fail
        await review.run_if_due()
        result, = await self.finished()
        self.assertEqual(result["status"], "failed")
        self.assertIn("provider rejected review", json.dumps(result))
        self.assertEqual(self.personal.read().version, 0)

    async def test_concurrent_user_edit_survives_review_version_conflict(self):
        # Review completion must not overwrite a newer user edit or report a failed write as saved.
        review = self.review()
        await self.make_due(review)
        self.llm.response = self.write_response(shared=True)

        async def user_edits_memory():
            self.shared.update(UpdateSharedMemory(version=0, content="用户刚修改的约定。"),
                               author="user", source="user", operation_id="user-edit")

        self.llm.during_call = user_edits_memory
        await review.run_if_due()
        self.assertEqual(self.shared.read().content, "用户刚修改的约定。")
        result, = await self.finished()
        self.assertEqual(result["status"], "failed")
        self.assertIn("SHARED_MEMORY_CHANGED", json.dumps(result))

    async def test_cancelling_model_call_records_cancellation_without_writing(self):
        review = self.review()
        await self.make_due(review)
        self.llm.response = self.write_response()
        started = asyncio.Event()

        async def wait_forever():
            started.set()
            await asyncio.Event().wait()

        self.llm.during_call = wait_forever
        task = asyncio.create_task(review.run_if_due())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.personal.read().version, 0)
        self.assertEqual((await self.finished())[-1]["status"], "cancelled")

    async def test_cancellation_after_committed_write_preserves_memory_and_reports_the_effect(self):
        # Interrupting a later audit write must not erase a completed edit or misreport it as no-op.
        review = self.review()
        await self.make_due(review)
        self.llm.response = self.write_response()
        recorded_write = asyncio.Event()
        original_record = self.runtime.receive_domain_fact

        async def block_result(session_id, fact_type, data, **kwargs):
            if fact_type == REVIEW_FACT and data["phase"] == "result:0":
                recorded_write.set()
                await asyncio.Event().wait()
            return await original_record(session_id, fact_type, data, **kwargs)

        self.runtime.receive_domain_fact = block_result
        task = asyncio.create_task(review.run_if_due())
        try:
            await asyncio.wait_for(recorded_write.wait(), timeout=1)
            self.assertEqual(self.personal.read().content, "代码审查先列阻断问题。")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        finally:
            self.runtime.receive_domain_fact = original_record
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.personal.read().version, 1)
        result, = await self.finished()
        self.assertEqual(result["status"], "cancelled")
        self.assertIn("PERSONAL_MEMORY_UPDATED", json.dumps(result))

    async def test_cancellation_after_finished_commit_keeps_confirmed_completion(self):
        # A Journal write may commit before delivering cancellation. Rewriting its
        # terminal delivery as cancelled would conflict and fail the whole Bot.
        review = self.review()
        await self.make_due(review)
        self.llm.response = self.write_response()
        finished_committed = asyncio.Event()
        original_record = self.runtime.receive_domain_fact

        async def block_after_finished_commit(session_id, fact_type, data, **kwargs):
            event = await original_record(session_id, fact_type, data, **kwargs)
            if fact_type == REVIEW_FACT and data["phase"] == "finished":
                finished_committed.set()
                await asyncio.Event().wait()
            return event

        self.runtime.receive_domain_fact = block_after_finished_commit
        task = asyncio.create_task(review.run_if_due())
        try:
            await asyncio.wait_for(finished_committed.wait(), timeout=1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)
        finally:
            self.runtime.receive_domain_fact = original_record
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        result, = await self.finished()
        self.assertEqual(result["status"], "updated")
        self.assertEqual(self.personal.read().version, 1)
        await review.run_if_due()
        self.assertEqual(len(self.llm.requests), 1)
