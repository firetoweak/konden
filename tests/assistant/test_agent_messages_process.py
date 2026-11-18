from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from functools import partial
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pytest

from konden.assistant.agent_messages import SEND_GROUP_MESSAGE, SEND_MESSAGE
from konden.assistant.attachments import AttachmentGateway
from konden.assistant.context.memory import memory_observations, SHARED_MEMORY, PERSONAL_MEMORY
from konden.assistant.agents import (
    AGENT_CREATED, COLLEAGUE_JOINED, GROUP_ATTENTION, PRIVATE_MESSAGE, parse_agent_fact,
)
from konden.assistant.host.session_store import SessionStore
from konden.assistant.host.supervisor import HostSupervisor
from konden.assistant.external_messages import EXTERNAL_MESSAGE, USER_SOURCE, PostExternal
from konden.assistant.shared_memory import READ_SHARED_MEMORY, UPDATE_SHARED_MEMORY, SharedMemoryStore
from konden.assistant.personal_memory import READ_PERSONAL_MEMORY, UPDATE_PERSONAL_MEMORY, personal_memory_store
from konden.assistant.memory_review import REVIEW_FACT
from konden.automation.routines import RoutineFire
from konden.llm.api import LLMCallResult, LLMResponse, LLMUsage, ToolCall
from konden.paths import KondenHome
from konden.runtime import AgentRuntime, CommandPhase, DomainFactCommitted, InvokeTool, ModelDecision, SqliteJournal, StepCommitted, ToolBinding, UserMessageReceived, replay
from tests.assistant.test_runner import ScriptedDecisionMaker
from tests.assistant.test_supervisor import until
from tests.fixtures.agent_messages import interrupted_send_config
from tests.fixtures.session_worker import ProcessLlm, config_for


pytestmark = pytest.mark.process


class MessagingLlm(ProcessLlm):
    def __init__(self, root):
        super().__init__(root)
        self.requests = []

    async def chat(self, messages, model, *, tools=None, **kwargs):
        self.requests.append({"messages": messages, "tools": tools})
        text = json.dumps(messages, ensure_ascii=False)
        called = {call["function"]["name"] for message in messages for call in message.get("tool_calls", ())}
        calls = ()
        attention = None
        # A passive group speech can follow the call before the decision freezes.
        # Answer calls since our last response, even when they are not the last message.
        for message in reversed(messages):
            if message["role"] == "assistant":
                break
            if f'"fact": "{GROUP_ATTENTION}"' in str(message.get("content")):
                attention = json.loads(message["content"])["data"]
                break
        if attention is not None:
            calls = (ToolCall("group", SEND_GROUP_MESSAGE, json.dumps(
                {"group": attention["group"], "text": "收到", "reply_to": attention["message_id"]})),)
        elif "PERSONAL_WRITE:" in text:
            if READ_PERSONAL_MEMORY not in called:
                calls = (ToolCall("read-personal", READ_PERSONAL_MEMORY, "{}"),)
            elif UPDATE_PERSONAL_MEMORY not in called:
                current = next(json.loads(message["content"])["data"] for message in reversed(messages)
                               if message["role"] == "tool" and "PERSONAL_MEMORY_READ" in str(message["content"]))
                instruction = next(message["content"] for message in reversed(messages)
                                   if message["role"] == "user" and str(message["content"]).startswith("PERSONAL_WRITE:"))
                calls = (ToolCall("write-personal", UPDATE_PERSONAL_MEMORY, json.dumps({
                    "version": current["version"], "content": instruction.removeprefix("PERSONAL_WRITE:"),
                })),)
        elif "MEMORY_WRITE:" in text:
            if READ_SHARED_MEMORY not in called:
                calls = (ToolCall("read-memory", READ_SHARED_MEMORY, "{}"),)
            elif UPDATE_SHARED_MEMORY not in called:
                current = next(json.loads(message["content"])["data"] for message in reversed(messages)
                               if message["role"] == "tool" and "SHARED_MEMORY_READ" in str(message["content"]))
                instruction = next(message["content"] for message in reversed(messages)
                                   if message["role"] == "user" and str(message["content"]).startswith("MEMORY_WRITE:"))
                calls = (ToolCall("write-memory", UPDATE_SHARED_MEMORY, json.dumps({
                    "version": current["version"], "content": instruction.removeprefix("MEMORY_WRITE:"),
                })),)
        elif "SEND_TO_LIN" in text:
            if "list_colleagues" not in called:
                calls = (ToolCall("colleagues", "list_colleagues", "{}"),)
            elif SEND_MESSAGE not in called:
                calls = (ToolCall("send", SEND_MESSAGE, json.dumps({"recipient": "Lin", "text": "请核对接口"})),)
        elif "Kai" in text:
            if "list_colleagues" not in called:
                calls = (ToolCall("colleagues", "list_colleagues", "{}"),)
        return LLMCallResult(LLMResponse(content="" if calls else "done", calls=calls),
                             LLMUsage(input_tokens=10, output_tokens=5))


class AgentMessagesProcessTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.home = KondenHome(self.root / "home")
        self.store = SessionStore(self.home.runtime_sessions_root)
        self.llm = MessagingLlm(self.root)
        self.host = self.new_host()
        self.ada = await self.host.create_agent(name="Ada")
        self.lin = await self.host.create_agent(name="Lin")
        # Creation wakes each Agent once; tests start from settled, offline Agents.
        for agent in (self.ada, self.lin):
            await self.settled(agent)
            await until(lambda: agent.session_id not in self.host.workers)

    def new_host(self, when=None):
        factory = partial(config_for, self.root) if when is None else partial(interrupted_send_config, self.root, when)
        return HostSupervisor(self.store, factory, self.home, None, llm=self.llm)

    async def asyncTearDown(self):
        await self.host.close()
        self.directory.cleanup()

    async def events(self, agent):
        return await SqliteJournal(self.store.require(agent.session_id)).snapshot(agent.session_id)

    async def settled(self, agent):
        await asyncio.wait_for(self.host.wait_quiescent(agent.session_id), 30)

    async def test_created_agent_wakes_without_human_and_joining_reaches_colleagues(self):
        steps = {agent.session_id: sum(isinstance(e.payload, StepCommitted) for e in await self.events(agent))
                 for agent in (self.ada, self.lin)}
        self.assertTrue(all(steps.values()))
        kai = await self.host.create_agent(name="Kai")
        await self.settled(kai)
        for agent in (self.ada, self.lin):
            await self.settled(agent)
        self.assertTrue(any(f'"fact": "{AGENT_CREATED}"' in str(message["content"])
                            and '"name": "Kai"' in str(message["content"])
                            for r in self.llm.requests for message in r["messages"]))
        self.assertEqual([entry.name for entry in await self.host.agents.list()], ["Ada", "Kai", "Lin"])
        for agent in (self.ada, self.lin):
            events = await self.events(agent)
            facts = [(e.payload.fact_type, parse_agent_fact(e.payload).name) for e in events
                     if isinstance(e.payload, DomainFactCommitted)
                     and e.payload.fact_type == COLLEAGUE_JOINED]
            self.assertEqual(facts[-1:], [(COLLEAGUE_JOINED, "Kai")])
            # Roster changes are visible later but do not make colleagues decide.
            self.assertEqual(sum(isinstance(e.payload, StepCommitted) for e in events), steps[agent.session_id])
        self.assertTrue(self.host.failures.empty())

    async def test_plain_user_and_routine_work_records_stay_private_without_waking_colleague(self):
        outputs = []
        self.host.sink = lambda *args: outputs.append(args)
        before = await self.events(self.lin)
        await self.host.receive_user_message(self.ada.session_id, "只回复 done", delivery_id="plain")
        await self.settled(self.ada)
        now = datetime.now(timezone.utc)
        await self.host._deliver_routine(RoutineFire(
            fire_id="routine-fire", routine_id="routine", session_id=self.ada.session_id,
            name="检查", intent="报告状态", planned_at=now, fired_at=now))
        await self.settled(self.ada)
        self.assertEqual(outputs, [])
        self.assertEqual(await self.events(self.lin), before)
        self.assertNotIn(self.lin.session_id, self.host.workers)
        self.assertTrue(self.host.failures.empty())

    async def test_restart_delivers_accepted_three_party_user_input_to_one_worker_once(self):
        """Accepted Web input must reach a real Worker even if its first delivery was interrupted."""
        service = self.host.external_messages
        connection, _ = service.connections.create("test")
        identity = "1" * 32
        await service.context(connection, identity, "恢复测试")
        before = await self.events(self.lin)
        file = AttachmentGateway(self.store.root).for_session(self.ada.session_id).save_file_stream(BytesIO(b"attachment recovery"), "恢复材料.zip")
        message = PostExternal(bot_session_id=self.ada.session_id, text="三方输入恢复检查", target="bot", delivery_id="human",
                               artifact_refs=[file.attachment_id])
        with patch.object(self.host, "receive_user_message", side_effect=RuntimeError("delivery interrupted")):
            with self.assertRaisesRegex(RuntimeError, "delivery interrupted"):
                await service.post_user(identity, message)
        await self.host.close()
        self.host = self.new_host()
        self.llm.requests.clear()
        await self.host.resume_unfinished_sessions()
        await self.settled(self.ada)
        inputs = [event for event in await self.events(self.ada) if isinstance(event.payload, UserMessageReceived)
                  and event.delivery is not None and event.delivery.source == USER_SOURCE]
        self.assertEqual(len(inputs), 1)
        self.assertEqual(inputs[0].artifact_refs, (file.attachment_id,))
        # The Worker sees one genuine user input, not two apparent messages from one action.
        shown = self.llm.requests[-1]["messages"]
        self.assertEqual(sum("三方输入恢复检查" in str(item.get("content")) for item in shown), 1)
        self.assertTrue(any(item["role"] == "user" and "三方输入恢复检查" in str(item.get("content")) for item in shown))
        self.assertIn("恢复材料.zip", str(shown))
        self.assertEqual(await self.events(self.lin), before)
        await self.host.external_messages.post_user(identity, message)
        await self.settled(self.ada)
        accepted = [event for event in await self.events(self.ada) if isinstance(event.payload, DomainFactCommitted)
                    and event.payload.fact_type == EXTERNAL_MESSAGE]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0].artifact_refs, (file.attachment_id,))
        self.assertTrue(self.host.failures.empty())

    async def test_shared_and_personal_memories_keep_their_scope_across_rename_and_restart(self):
        shared = SharedMemoryStore(self.home.state_root / "shared_memory.sqlite")
        idle = await self.events(self.lin)
        await self.host.receive_user_message(self.ada.session_id, "MEMORY_WRITE:共享资源先协调。", delivery_id="ada-memory")
        await self.settled(self.ada)
        self.assertEqual(await self.events(self.lin), idle)
        self.assertNotIn(self.lin.session_id, self.host.workers)
        self.assertEqual(shared.read().content, "共享资源先协调。")
        await self.host.receive_user_message(self.lin.session_id, "MEMORY_WRITE:共享资源先协调，交接附验证依据。", delivery_id="lin-memory")
        await self.settled(self.lin)
        note = shared.read().model_dump()
        self.assertEqual(note, {"version": 2, "content": "共享资源先协调，交接附验证依据。", "updated_by": "Lin"})
        await self.host.receive_user_message(self.ada.session_id, "查看同事的新约定。", delivery_id="observe-memory")
        await self.settled(self.ada)
        self.assertEqual(shared.read().model_dump(), note)
        self.assertIn("共享资源先协调，交接附验证依据。", json.dumps(self.llm.requests[-1]["messages"], ensure_ascii=False))
        idle = await self.events(self.lin)
        await self.host.receive_user_message(self.ada.session_id, "PERSONAL_WRITE:先给判断，再解释取舍。", delivery_id="ada-personal")
        await self.settled(self.ada)
        own = personal_memory_store(self.home, self.ada).read().model_dump()
        self.assertEqual(own["content"], "先给判断，再解释取舍。")
        self.assertEqual(await self.events(self.lin), idle)
        self.assertEqual(personal_memory_store(self.home, self.lin).read().content, "")
        self.assertEqual(shared.read().model_dump(), note)
        await self.host.rename_agent(self.ada.session_id, "Aster")
        await self.host.close()
        self.host = self.new_host()
        await self.host.receive_user_message(self.ada.session_id, "按当前约束继续工作。", delivery_id="after-restart")
        await self.settled(self.ada)
        self.assertEqual(personal_memory_store(self.home, self.ada).read().model_dump(), own)
        shown = json.dumps(self.llm.requests[-1]["messages"], ensure_ascii=False)
        self.assertIn("先给判断，再解释取舍。", shown)
        self.assertIn("共享资源先协调，交接附验证依据。", shown)
        observed = memory_observations(await self.events(self.ada))
        self.assertEqual(observed[SHARED_MEMORY], note)
        self.assertEqual(observed[PERSONAL_MEMORY], own)
        self.assertTrue(self.host.failures.empty())

    async def test_background_memory_crosses_worker_pipe_and_yields_to_new_input(self):
        # The foreground never calls memory tools. This proves the real Worker /
        # Host path, persistence and IPC cancellation, not model judgment quality.
        note = "代码审查时先报告阻断问题。"
        shared = SharedMemoryStore(self.home.state_root / "shared_memory.sqlite")
        personal = personal_memory_store(self.home, self.ada)
        initial_shared = shared.read().model_dump()
        foreground = []
        reviews = []
        outputs = []
        review_blocked = asyncio.Event()
        review_aborted = asyncio.Event()
        release_late_result = asyncio.Event()
        self.host.sink = lambda *args: outputs.append(args)

        async def chat(messages, model, *, tools=None, **kwargs):
            names = {tool["function"]["name"] for tool in tools or ()}
            if names != {UPDATE_PERSONAL_MEMORY, UPDATE_SHARED_MEMORY}:
                if messages[-1]["role"] == "tool":
                    return LLMCallResult(LLMResponse(content="已交付"), LLMUsage(10, 5))
                foreground.append(messages)
                return LLMCallResult(LLMResponse(calls=(ToolCall(
                    "reply", SEND_MESSAGE, '{"recipient":"user","text":"done"}',
                ),)), LLMUsage(10, 5))
            material = json.loads(messages[-1]["content"])
            reviews.append(material)
            if len(reviews) == 1:
                content = note
            elif len(reviews) == 2:
                review_blocked.set()
                try:
                    await release_late_result.wait()
                except asyncio.CancelledError:
                    review_aborted.set()
                    raise
                content = "迟到的结果不应覆盖已有记忆。"
            else:
                return LLMCallResult(LLMResponse(content="无需更新"), LLMUsage(10, 5))
            return LLMCallResult(LLMResponse(calls=(ToolCall(
                "remember", UPDATE_PERSONAL_MEMORY, json.dumps({
                    "version": material["memories"]["personal"]["version"],
                    "content": content,
                }, ensure_ascii=False),
            ),)), LLMUsage(10, 5))

        async def send(text, number):
            await self.host.receive_user_message(self.ada.session_id, text, delivery_id=f"review-{number}")
            # A foreground reply is independent from the following review's
            # completion; wait for that review through its actual Journal fact.
            await until(lambda: len(outputs) == number)

        async def finished_reviews(count):
            async with asyncio.timeout(30):
                while True:
                    finished = [event.payload.data for event in await self.events(self.ada)
                                if isinstance(event.payload, DomainFactCommitted)
                                and event.payload.fact_type == REVIEW_FACT
                                and event.payload.data["phase"] == "finished"]
                    if len(finished) >= count:
                        return finished
                    await asyncio.sleep(.02)

        initial_messages = (
            "以后你做代码审查，先告诉我阻断问题。",
            "我们先讨论接口边界。",
            "这次回答短一点。",
            "先把风险说清楚。",
            "继续看看当前方案。",
        )
        with patch.object(self.llm, "chat", side_effect=chat):
            try:
                for number, text in enumerate(initial_messages, 1):
                    await send(text, number)
                first, = await finished_reviews(1)
                self.assertEqual(first["status"], "updated")
                self.assertEqual(personal.read().content, note)
                self.assertEqual(shared.read().model_dump(), initial_shared)
                self.assertEqual(personal_memory_store(self.home, self.lin).read().content, "")
                self.assertEqual(len(reviews), 1)
                self.assertEqual(reviews[0]["bot"], "Ada")
                self.assertEqual([message["text"] for message in reviews[0]["material"]["messages"]
                                  if message["sender"]["kind"] == "user"], list(initial_messages))

                await send("开始下一次审查。", 6)
                shown = json.dumps(foreground[-1], ensure_ascii=False)
                self.assertIn(note, shown)
                self.assertNotIn(REVIEW_FACT, shown)

                for number, text in enumerate((
                    "我们再看一下模块职责。", "接着检查数据流。",
                    "再确认一下错误处理。", "继续讨论验证方式。",
                ), 7):
                    await send(text, number)
                await asyncio.wait_for(review_blocked.wait(), 30)
                saved = personal.read().model_dump()

                await send("先回答这个新问题。", 11)
                await asyncio.wait_for(review_aborted.wait(), 30)
                release_late_result.set()
                finished = await finished_reviews(3)
                self.assertEqual([item["status"] for item in finished],
                                 ["updated", "cancelled", "unchanged"])
                self.assertEqual(personal.read().model_dump(), saved)
                self.assertEqual(shared.read().model_dump(), initial_shared)
                self.assertIn("先回答这个新问题。", json.dumps(foreground[-1], ensure_ascii=False))
                self.assertTrue(all(REVIEW_FACT not in json.dumps(request, ensure_ascii=False)
                                    for request in foreground))
                self.assertEqual([output[2] for output in outputs], ["done"] * 11)
                self.assertTrue(self.host.failures.empty())
            finally:
                release_late_result.set()

    async def test_group_members_receive_calls_even_after_current_turn_interruption(self):
        await self.host.cancel_turn(self.lin.session_id)
        outputs = []
        self.host.sink = lambda *args: outputs.append(args)
        group = await self.host.agent_groups.create(name="开发组",
                                                    members=["Ada", "Lin"])
        await self.host.agent_groups.post(group.group_id, "大家同步一下进度", [])
        # Busy members can speak after the drive temporarily runs out of queued calls.
        # Wait for the expected public replies, not that intermediate scheduler state.
        async with asyncio.timeout(60):
            while len(await self.host.agent_groups.messages(group.group_id)) < 7:
                await asyncio.sleep(0.02)
            await until(lambda: not self.host.agent_groups._drives[group.group_id].running)
        for agent in (self.ada, self.lin):
            await self.settled(agent)
        speakers = [message.sender.name for message in await self.host.agent_groups.messages(group.group_id)]
        # Every speech without a mention calls the other member, until the round limit.
        self.assertEqual(speakers[0], None)
        self.assertEqual(sorted(speakers[1:]), ["Ada"] * 3 + ["Lin"] * 3)
        self.assertEqual(outputs, [])
        for agent in (self.ada, self.lin):
            attentions = [e for e in await self.events(agent) if isinstance(e.payload, DomainFactCommitted)
                          and e.payload.fact_type == GROUP_ATTENTION]
            self.assertEqual(len(attentions), 3)
        self.assertTrue(self.host.failures.empty())

    async def test_group_delivery_continues_while_a_called_member_is_still_deciding(self):
        group = await self.host.agent_groups.create(name="开发组", members=["Ada", "Lin"])
        entered, release = asyncio.Event(), asyncio.Event()
        route = self.host._route

        async def held(operation, session_id, arguments, *, worker=None):
            if operation == "llm_chat" and session_id == self.ada.session_id:
                entered.set()
                await release.wait()
            return await route(operation, session_id, arguments, worker=worker)

        async def lin_has_replied_to(message_id):
            return any(message.sender.name == "Lin" and message.reply_to == message_id
                       for message in await self.host.agent_groups.messages(group.group_id))

        with patch.object(self.host, "_route", side_effect=held):
            first = await self.host.agent_groups.post(group.group_id, "两位验收第一版", [])
            try:
                await asyncio.wait_for(entered.wait(), 30)
                async with asyncio.timeout(30):
                    while not await lin_has_replied_to(first.message_id):
                        await asyncio.sleep(.02)
                second = await self.host.agent_groups.post(group.group_id, "Lin 再看下一版", ["Lin"])
                async with asyncio.timeout(30):
                    while not await lin_has_replied_to(second.message_id):
                        await asyncio.sleep(.02)
                self.assertFalse(release.is_set())
            finally:
                release.set()
            for agent in (self.ada, self.lin):
                await self.settled(agent)
        self.assertTrue(self.host.failures.empty())

    async def test_interrupted_recipient_processes_new_peer_message_automatically(self):
        await self.host.cancel_turn(self.lin.session_id)
        before = await self.events(self.lin)
        await self.host.receive_user_message(self.ada.session_id, "SEND_TO_LIN", delivery_id="send")
        await self.settled(self.ada)
        await self.settled(self.lin)
        after = await self.events(self.lin)
        messages = [event for event in after if isinstance(event.payload, DomainFactCommitted)
                    and event.payload.fact_type == PRIVATE_MESSAGE]
        self.assertEqual(len(messages), 1)
        self.assertGreater(sum(isinstance(e.payload, StepCommitted) for e in after),
                           sum(isinstance(e.payload, StepCommitted) for e in before))
        command = next(item for item in replay(self.ada.session_id, await self.events(self.ada)).state.commands
                       if item.command.effect.name == SEND_MESSAGE)
        self.assertEqual(command.outcome.value["data"]["notice"], "已送达；不等待对方回复。")
        for request in self.llm.requests:
            self.assertNotIn(self.ada.session_id, json.dumps(request["messages"]))
            self.assertIn("link_project", {tool["function"]["name"] for tool in request["tools"]})
        self.assertTrue(self.host.failures.empty())

    async def test_offline_recipient_is_started_and_can_observe_attributed_message(self):
        outputs = []
        self.host.sink = lambda *args: outputs.append(args)
        self.assertNotIn(self.lin.session_id, self.host.workers)
        await self.host.receive_user_message(self.ada.session_id, "SEND_TO_LIN", delivery_id="send")
        await self.settled(self.ada)
        await self.settled(self.lin)
        messages = [parse_agent_fact(e.payload) for e in await self.events(self.lin)
                    if isinstance(e.payload, DomainFactCommitted) and e.payload.fact_type == PRIVATE_MESSAGE]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].sender.name, "Ada")
        self.assertTrue(any(isinstance(e.payload, StepCommitted) for e in await self.events(self.lin)))
        self.assertTrue(any("请核对接口" in json.dumps(r["messages"], ensure_ascii=False) for r in self.llm.requests))
        self.assertEqual(outputs, [])
        self.assertFalse(any(isinstance(e.payload, DomainFactCommitted) and e.payload.fact_type == PRIVATE_MESSAGE
                             for e in await self.events(self.ada)))
        self.assertTrue(self.host.failures.empty())

    async def test_real_sender_crash_recovers_one_delivery_and_a_known_original_outcome(self):
        for when in ("before", "after"):
            with self.subTest(when=when):
                await self.host.close()
                # Each crash exercises a fresh sender with a fresh Command.
                self.host = self.new_host(when)
                sender = await self.host.create_agent(name=f"Sender-{when}")
                await self.host.receive_user_message(sender.session_id, "SEND_TO_LIN", delivery_id="send")
                failure = await asyncio.wait_for(self.host.wait_failure(), 30)
                self.assertEqual(failure.session_id, sender.session_id)
                await self.host.close()
                self.host = self.new_host()
                await self.host.resume(sender.session_id)
                await self.settled(sender)
                await self.settled(self.lin)
                command = next(item for item in replay(sender.session_id, await self.events(sender)).state.commands
                               if item.command.effect.name == SEND_MESSAGE)
                self.assertIsNot(command.phase, CommandPhase.UNKNOWN)
                self.assertEqual(command.outcome.value["code"], "DELIVERED")
                received = [parse_agent_fact(e.payload) for e in await self.events(self.lin)
                            if isinstance(e.payload, DomainFactCommitted) and e.payload.fact_type == PRIVATE_MESSAGE]
                self.assertEqual(sum(m.sender.name == sender.name for m in received), 1)
                self.assertTrue(self.host.failures.empty())

    async def test_two_workers_recovering_mutual_sends_can_accept_facts_before_deciding(self):
        for sender, recipient in ((self.ada, self.lin), (self.lin, self.ada)):
            entered = asyncio.Event()

            async def interrupted(context, arguments):
                entered.set()
                await asyncio.Event().wait()

            journal = SqliteJournal(self.store.require(sender.session_id))
            runtime = AgentRuntime(journal, ScriptedDecisionMaker((lambda _, name=recipient.name: ModelDecision(
                command_requests=(InvokeTool(SEND_MESSAGE, (("recipient", name), ("text", "恢复中的私信"))),)),)),
                {SEND_MESSAGE: ToolBinding(interrupted)})
            await runtime.receive_user_message(sender.session_id, "互发私信", delivery_id="mutual")
            await runtime.advance(sender.session_id)
            await runtime.dispatcher.start_pending(sender.session_id)
            await asyncio.wait_for(entered.wait(), 3)
            await runtime.dispatcher.close()
        await asyncio.wait_for(asyncio.gather(
            self.host.resume(self.ada.session_id), self.host.resume(self.lin.session_id)), 30)
        await self.settled(self.ada)
        await self.settled(self.lin)
        for agent in (self.ada, self.lin):
            events = await self.events(agent)
            messages = [e for e in events if isinstance(e.payload, DomainFactCommitted)
                        and e.payload.fact_type == PRIVATE_MESSAGE]
            self.assertEqual(len(messages), 1)
            command, = [item for item in replay(agent.session_id, events).state.commands
                        if item.command.effect.name == SEND_MESSAGE]
            self.assertEqual(command.outcome.value["code"], "DELIVERED")
        self.assertTrue(self.host.failures.empty())
