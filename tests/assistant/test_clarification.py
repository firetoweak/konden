import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from konden.assistant.agents import AgentCreated, fact_arguments
from konden.assistant.assembly import build_assistant_assembly
from konden.assistant.clarification import (
    ANSWER_SOURCE,
    ASK_USER,
    CLARIFICATION_OPTIONS,
    TimedQuestion,
    clarification_binding,
    clarification_reply,
    recover_clarification_attempts,
)
from konden.assistant.conversations import project_conversation
from konden.assistant.host.session_store import SessionStore
from konden.assistant.inputs import InputConflict
from konden.automation.once import OneShotSchedules
from konden.config import AssistantConfig
from konden.llm.api import LLMCallResult, LLMResponse, LLMUsage, ToolCall
from konden.paths import KondenHome
from konden.runtime import (
    AgentRuntime,
    CommandOutcomeReceived,
    InvokeTool,
    ModelDecision,
    SqliteJournal,
)
from konden.runtime.step import RecordedDecision
from tests.assistant.test_runner import ScriptedDecisionMaker
from tests.fixtures.workspaces import workspace_record
from tests.session_scheduler import SettlingScheduler


def test_answer_stays_with_question_and_cannot_grant_pending_authorization(tmp_path):
    asyncio.run(clarification_round_trip(tmp_path))


async def clarification_round_trip(root):
    home = KondenHome(root / "home")
    directory = root / "workspace"
    directory.mkdir()
    sid = "ada"
    store = SessionStore(home.runtime_sessions_root)
    await store.create(sid, directory=directory, initial_fact=fact_arguments(
        AgentCreated(name="Ada", directory=str(directory)), delivery_id="identity"))
    llm = AsyncMock()
    llm.chat.side_effect = [LLMCallResult(response, LLMUsage(input_tokens=1, output_tokens=1)) for response in (
        LLMResponse(content="你好。"),
        LLMResponse(calls=(ToolCall("bad", "ask_user", '{"question":" "}'),)),
        LLMResponse(content="私有工作笔记", calls=(ToolCall("ask", "ask_user", '{"question":"保留附录吗？","options":["yes","no"]}'),)),
        LLMResponse(content="私有工作笔记", calls=(ToolCall("report", "send_message", '{"recipient":"user","text":"周报正文已经整理好了。"}'),)),
        LLMResponse(content="结束工作"),
        LLMResponse(calls=(ToolCall("write", "write_file", '{"path":"note.txt","content":"unapproved"}'),)),
        LLMResponse(content="会保留附录。"),
    )]
    emitted = []
    assembly = await build_assistant_assembly(
        AssistantConfig(model_name="test", compact_threshold_tokens=200000, llm=llm),
        lambda *args: emitted.append(args[2]), store.journal(sid), session_id=sid, workspace=workspace_record(directory),
        home=home, scheduler_factory=SettlingScheduler)
    sessions = assembly.sessions

    async def conversation():
        await assembly.scheduler.join()
        events, state = await store.projected(sid)
        return project_conversation(sid, events, state.steps, session=await sessions.view(sid))

    try:
        await sessions.resume(sid)
        await assembly.scheduler.join()
        await sessions.accept_input(sid, "整理周报", delivery_id="report")
        asked = await conversation()
        card = next(item.clarification for item in asked.items if getattr(item, "clarification", None))
        assert card.options == ("yes", "no") and card.answer is None
        assert card.deadline is None
        assert emitted == ["保留附录吗？", "周报正文已经整理好了。"]
        assert [i.text for i in asked.items if i.kind == "step" and i.text] == emitted
        await sessions.accept_input(sid, "另外写一个文件", delivery_id="write")
        pending = (await conversation()).session.pending_authorization_ids
        assert pending
        with pytest.raises(InputConflict):
            await sessions.accept_input(sid, "yes", delivery_id="unknown", source=ANSWER_SOURCE)
        for _ in range(2):
            await sessions.accept_input(sid, "yes", delivery_id=card.question_id, source=ANSWER_SOURCE)
        with pytest.raises(InputConflict):
            await sessions.accept_input(sid, "no", delivery_id=card.question_id, source=ANSWER_SOURCE)
        answered = await conversation()
        assert answered.session.pending_authorization_ids == pending
        assert [item.text for item in answered.items if item.kind == "user"].count("yes") == 1
        assert [item.clarification.answer for item in answered.items if getattr(item, "clarification", None)] == ["yes"]
        assert any(msg["role"] == "user" and "保留附录吗？" in msg["content"] and msg["content"].endswith("yes")
                   for msg in llm.chat.call_args.args[0])
    finally:
        await assembly.scheduler.close()


def test_recovery_after_timer_registration_keeps_original_deadline_and_one_receipt(tmp_path):
    asyncio.run(recover_registered_timer(tmp_path))


async def recover_registered_timer(root):
    sid = "report"
    journal = SqliteJournal(root / "journal.sqlite")
    clock = OneShotSchedules(root / "clock.sqlite")
    args = {"question": "图表放哪里？", "deadline": {"after_seconds": 30, "plan": "先放附录"}}
    decision, options = clarification_reply(ModelDecision(command_requests=(InvokeTool(ASK_USER, tuple(args.items())),)), TimedQuestion)
    recorded = RecordedDecision(decision, decision_metadata={CLARIFICATION_OPTIONS: options})
    registrations = []

    async def register(_operation, session_id, request):
        start = datetime.fromisoformat(request["started_at"])
        schedule = clock.register(request["schedule_id"], session_id, request["delay_seconds"], request["purpose"], started_at=start)
        registrations.append((schedule.schedule_id, start, schedule.due_at))
        return {"due_at": schedule.due_at.isoformat()}

    async def interrupted(*request):
        await register(*request)
        raise RuntimeError("lost receipt")

    runtime = AgentRuntime(journal, ScriptedDecisionMaker((lambda _: recorded,)), {ASK_USER: clarification_binding(journal, interrupted)})
    scheduler = SettlingScheduler(runtime, sid)
    await runtime.create_session(sid)
    await runtime.receive_user_message(sid, "整理周报", delivery_id="start")
    try:
        await scheduler.wake(sid)
        with pytest.raises(RuntimeError, match="lost receipt"):
            await scheduler.join()
    finally:
        await scheduler.close()
    await recover_clarification_attempts(journal, sid, register)
    await recover_clarification_attempts(journal, sid, register)
    assert len(registrations) == 2 and registrations[0] == registrations[1]
    _, started, due = registrations[0]
    assert due == started + timedelta(seconds=30)
    outcomes = [e.payload.outcome for e in await journal.snapshot(sid) if isinstance(e.payload, CommandOutcomeReceived)]
    assert [outcome.value["code"] for outcome in outcomes] == ["QUESTION_SENT"]
