import asyncio
from unittest.mock import AsyncMock

import pytest

from konden.assistant.agents import AgentCreated, fact_arguments
from konden.assistant.assembly import build_assistant_assembly
from konden.assistant.clarification import ANSWER_SOURCE
from konden.assistant.conversations import project_conversation
from konden.assistant.host.session_store import SessionStore
from konden.assistant.inputs import InputConflict
from konden.config import AssistantConfig
from konden.llm.api import LLMCallResult, LLMResponse, LLMUsage, ToolCall
from konden.paths import KondenHome
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
        LLMResponse(calls=(ToolCall("ask", "ask_user", '{"question":"保留附录吗？","options":["yes","no"]}'),)),
        LLMResponse(calls=(ToolCall("write", "write_file", '{"path":"note.txt","content":"unapproved"}'),)),
        LLMResponse(content="会保留附录。"),
    )]
    assembly = await build_assistant_assembly(
        AssistantConfig(model_name="test", compact_threshold_tokens=200000, llm=llm),
        lambda *_args: None, store.journal(sid), session_id=sid, workspace=workspace_record(directory),
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
