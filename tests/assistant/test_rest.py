import asyncio
from unittest.mock import AsyncMock

from konden.assistant.agents import AgentCreated, fact_arguments
from konden.assistant.assembly import build_assistant_assembly
from konden.assistant.conversations import project_conversation
from konden.assistant.host.session_store import SessionStore
from konden.config import AssistantConfig
from konden.llm.api import LLMCallResult, LLMResponse, LLMUsage, ToolCall
from konden.paths import KondenHome
from konden.runtime import RuntimeStatus
from tests.fixtures.workspaces import workspace_record
from tests.session_scheduler import SettlingScheduler


async def _bot(root, *responses):
    home = KondenHome(root / "home")
    directory = root / "workspace"
    directory.mkdir()
    store = SessionStore(home.runtime_sessions_root)
    await store.create("ada", directory=directory, initial_fact=fact_arguments(
        AgentCreated(name="Ada", directory=str(directory)), delivery_id="identity"))
    llm = AsyncMock()
    llm.chat.side_effect = [LLMCallResult(response, LLMUsage(input_tokens=1, output_tokens=1)) for response in responses]
    failures = []
    assembly = await build_assistant_assembly(
        AssistantConfig(model_name="test", compact_threshold_tokens=200000, llm=llm),
        lambda *_args: None, store.journal("ada"), session_id="ada", workspace=workspace_record(directory),
        home=home, scheduler_factory=SettlingScheduler,
        session_failed_sink=lambda _session_id, text: failures.append(text),
    )
    return store, llm, assembly, failures


def test_bot_asks_and_rests_in_one_batch_without_another_model_call(tmp_path):
    async def run():
        store, llm, assembly, _failures = await _bot(
            tmp_path,
            LLMResponse(calls=(ToolCall("greet", "rest", "{}"),)),
            LLMResponse(calls=(
                ToolCall("ask", "ask_user", '{"question":"喜欢哪种风格？","options":["简洁","诗意"]}'),
                ToolCall("done", "rest", "{}"),
            )),
        )
        try:
            await assembly.sessions.resume("ada")
            await assembly.scheduler.join()
            await assembly.sessions.accept_input("ada", "起个名字", delivery_id="name")
            await assembly.scheduler.join()
            assert llm.chat.await_count == 2
            assert {call.kwargs["tool_choice"] for call in llm.chat.await_args_list} == {"required"}
            events, state = await store.projected("ada")
            view = project_conversation("ada", events, state.steps, session=await assembly.sessions.view("ada"))
            cards = [item.clarification for item in view.items if getattr(item, "clarification", None)]
            assert [card.options for card in cards] == [("简洁", "诗意")]
            assert not any(tool.name == "rest" for item in view.items for tool in getattr(item, "tools", ()))
        finally:
            await assembly.scheduler.close()

    asyncio.run(run())


def test_bot_reply_without_any_tool_call_fails_visibly_instead_of_resting(tmp_path):
    async def run():
        store, _llm, assembly, failures = await _bot(tmp_path, LLMResponse(content="你好。"))
        try:
            await assembly.sessions.resume("ada")
            await assembly.scheduler.join()
            assert len(failures) == 1 and "没有调用任何工具" in failures[0]
            _events, state = await store.projected("ada")
            assert state.status is RuntimeStatus.RUNNABLE
        finally:
            await assembly.scheduler.close()

    asyncio.run(run())
