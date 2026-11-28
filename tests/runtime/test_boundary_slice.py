from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
import tempfile
import unittest

from konden.runtime import (
    AgentRuntime,
    DeliveryIdentity,
    DomainFactCommitted,
    EventDraft,
    InvokeTool,
    ModelDecision,
    RuntimeStatus,
    SqliteJournal,
    ToolBinding,
    UserMessageReceived,
    diagnose_artifacts,
    replay,
)
from tests.assistant.test_runner import ScriptedDecisionMaker, SequentialIds
from tests.fixtures.memory_journal import MemoryJournal

NOW = datetime(2026, 8, 21, 14, 0, tzinfo=UTC)


class RecordingTool:
    def __init__(self, name: str) -> None:
        self.name = name
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.executions: list[Mapping[str, object]] = []

    def binding(self) -> dict[str, ToolBinding]:
        return {
            self.name: ToolBinding(
                self._handler,
            )
        }

    async def _handler(self, _context, arguments: Mapping[str, object]) -> object:
        self.executions.append(arguments)
        self.started.set()
        await self.release.wait()
        return f"{self.name}-result"


def _requesting(name: str, content: str = "request", *follow_ups: str) -> ScriptedDecisionMaker:
    return ScriptedDecisionMaker((
        lambda _frame: ModelDecision(content=content, command_requests=(InvokeTool(name),)),
        *(lambda _frame, text=text: ModelDecision(content=text) for text in follow_ups),
    ))


async def _drain(runtime) -> None:
    while runtime.dispatcher.active_count:
        await asyncio.sleep(0)


def runtime_for(
    tool: RecordingTool,
    model: ScriptedDecisionMaker,
    journal=None,
):
    return AgentRuntime(
        journal or MemoryJournal(),
        model,
        tool.binding(),
        SequentialIds(),
    )


class AgentRuntimeBoundarySliceTest(unittest.IsolatedAsyncioTestCase):
    SESSION_ID = "boundary-session"

    async def test_stale_advance_position_does_not_claim_or_consume_decision(self):
        for persistent in (False, True):
            with self.subTest(persistent=persistent), tempfile.TemporaryDirectory() as root:
                journal = SqliteJournal(Path(root) / "journal.sqlite") if persistent else MemoryJournal()
                model = ScriptedDecisionMaker((lambda _: ModelDecision(content="done"),))
                runtime = runtime_for(RecordingTool("read"), model, journal)
                await runtime.receive_user_message(self.SESSION_ID, "first", delivery_id="first")
                checked = await runtime.snapshot(self.SESSION_ID)
                await runtime.receive_user_message(self.SESSION_ID, "later", delivery_id="later")
                current = await runtime.snapshot(self.SESSION_ID)

                deferred = await runtime.advance(
                    self.SESSION_ID, expected_journal_position=checked[-1].sequence
                )
                self.assertIsNone(deferred.step)
                self.assertEqual(deferred.status, RuntimeStatus.RUNNABLE)
                self.assertEqual(model.frames, [])
                self.assertEqual(await runtime.snapshot(self.SESSION_ID), current)

                committed = await runtime.advance(
                    self.SESSION_ID, expected_journal_position=current[-1].sequence
                )
                self.assertIsNotNone(committed.step)
                self.assertEqual(model.frames[0].observed_journal_position, current[-1].sequence)

    async def test_decision_content_is_not_delivered_user_message(self):
        tool = RecordingTool("deliver")
        runtime = runtime_for(tool, ScriptedDecisionMaker((
            lambda _frame: ModelDecision(
                content="任务完成",
                command_requests=(InvokeTool("deliver", (("text", "hello user"),)),),
            ),
        )))
        inbound = await runtime.receive_user_message(self.SESSION_ID, "say hello", delivery_id="ask-1")
        step = (await runtime.advance(self.SESSION_ID)).step
        await asyncio.wait_for(tool.started.wait(), timeout=1)
        tool.release.set()
        await _drain(runtime)

        events = await runtime._journal.snapshot(self.SESSION_ID)
        self.assertEqual(step.decision.content, "任务完成")
        self.assertEqual(tool.executions, [{"text": "hello user"}])
        self.assertEqual(
            [e.event_id for e in events if isinstance(e.payload, UserMessageReceived)],
            [inbound.event_id],
        )

    async def test_missing_artifact_degrades_replay_without_substitution(self):
        digest = "a" * 64
        journal = MemoryJournal()
        await journal.accept_delivery(
            EventDraft(
                event_id="message-1",
                session_id=self.SESSION_ID,
                payload=UserMessageReceived("read this"),
                occurred_at=NOW,
                artifact_refs=(digest,),
                delivery=DeliveryIdentity("user", "ask-1"),
            )
        )
        events = await journal.snapshot(self.SESSION_ID)
        uninspected = diagnose_artifacts(events)
        rebuilt = replay(self.SESSION_ID, events)
        inspected_empty = diagnose_artifacts(events, available_refs=())

        self.assertEqual(uninspected.refs, (digest,))
        self.assertEqual(uninspected.missing, ())
        self.assertFalse(uninspected.inspected)
        self.assertFalse(uninspected.complete)
        self.assertFalse(rebuilt.artifacts.inspected)
        self.assertEqual(rebuilt.artifacts.missing, ())
        self.assertTrue(inspected_empty.inspected)
        self.assertEqual(inspected_empty.missing, (digest,))
        self.assertFalse(inspected_empty.complete)
        self.assertTrue(diagnose_artifacts(events, available_refs=(digest,)).complete)

    async def test_domain_fact_requires_delivery_across_journals(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        journals = (
            ("memory", MemoryJournal()),
            ("sqlite", SqliteJournal(Path(temporary.name) / "fact.db")),
        )

        for name, journal in journals:
            with self.subTest(journal=name):
                with self.assertRaises(ValueError):
                    await journal.append(
                        EventDraft(
                            event_id=f"bare-fact-{name}",
                            session_id=self.SESSION_ID,
                            payload=DomainFactCommitted("probe", {"a": 1}),
                            occurred_at=NOW,
                        )
                    )
                accepted = await journal.accept_delivery(
                    EventDraft(
                        event_id=f"delivered-fact-{name}",
                        session_id=self.SESSION_ID,
                        payload=DomainFactCommitted("probe", {"a": 1}),
                        occurred_at=NOW,
                        delivery=DeliveryIdentity("probe", "probe-1"),
                    )
                )

                self.assertTrue(accepted.inserted)
                self.assertEqual(
                    accepted.event.delivery,
                    DeliveryIdentity("probe", "probe-1"),
                )
