"""Durable external identities and bidirectional messages over Bot timelines."""
from __future__ import annotations

import asyncio
from bisect import bisect_right, insort_right
from datetime import UTC, datetime
from functools import partial
from hashlib import sha256
from itertools import islice
import json
from pathlib import Path
from uuid import uuid4

from konden.assistant.attachments import (
    AttachmentGateway,
    AttachmentRejected,
    AttachmentSnapshot,
    is_file_attachment_id,
)
from konden.assistant.external_messages import (
    EXTERNAL_MESSAGE,
    USER_SOURCE,
    ConnectionStore,
    ExternalMessage,
    ExternalSession,
    ExternalSessionStore,
    external_direction,
    external_name,
    parse_external,
)
from konden.assistant.host.agent_messages import message_identity
from konden.assistant.host.agent_roster import AgentDeletedError, AgentNotFound
from konden.assistant.host.codex_app_server import CodexDisconnected, CodexLink
from konden.assistant.host.codex_chat import (
    list_codex_chats,
    native_codex_thread,
    prepare_codex_chat,
    read_codex_chat,
    send_codex_chat,
)
from konden.assistant.host.codex_continuation import (
    CodexContinuationInvalid,
    CodexContinuations,
    continuation_status,
)
from konden.assistant.host.codex_projects import CodexProjectTarget, read_codex_project
from konden.assistant.host.codex_titles import read_codex_threads, read_codex_titles
from konden.assistant.host.external_attachments import (
    describe_attachments,
    snapshot_files,
)
from konden.assistant.host.ipc import finish_on_cancel
from konden.runtime import AgentRuntime, DomainFactCommitted, Event, UserMessageReceived


class ExternalConnectionNotFound(LookupError):
    pass


class ExternalSessionNotFound(LookupError):
    pass


class ExternalMessageInvalid(ValueError):
    pass


class ExternalMessages:
    def __init__(self, host):
        self.host = host
        self.connections = ConnectionStore(host.home.root / "external_connections.json")
        self.sessions = ExternalSessionStore(host.home.root / "external_sessions.json")
        self.codex = CodexContinuations(host.home.root / "codex_continuations.json")
        self.changed_sink = None
        self.codex_link = CodexLink(self.notify_changed)
        self._sending = asyncio.Lock()
        self._changed = asyncio.Condition(self._sending)
        self._positions: dict[str, int] = {}
        self._conversations: dict[str, list[tuple[str, Event, ExternalMessage]]] = {}

    def _store(self, session_id):
        return AttachmentGateway(self.host.store.root).for_session(session_id)

    async def notify_changed(self):
        if self.changed_sink is not None:
            await self.changed_sink()

    def list_connections(self):
        return [item.view() for item in self.connections.read().connections]

    def rename_connection(self, connection_id, name):
        connection = self.connections.rename(connection_id, name)
        if connection is None:
            raise ExternalConnectionNotFound(connection_id)
        return connection.view()

    async def revoke_connection(self, connection_id):
        async with self._changed:
            if not any(item["connection_id"] == connection_id for item in self.list_connections()):
                raise ExternalConnectionNotFound(connection_id)
            self.connections.revoke(connection_id)
            for session in self.sessions.read().sessions:
                if session.connection_id == connection_id:
                    self.codex.configure(session.identity, None)
            self._changed.notify_all()
            await self.notify_changed()

    async def delete_session(self, identity):
        async with self._changed:
            if not any(session.identity == identity for session in self.sessions.read().sessions):
                raise ExternalSessionNotFound(identity)
            self.codex.configure(identity, None)
            self.sessions.remove(identity)
            self._changed.notify_all()
            await self.notify_changed()

    async def prepare_local_codex(self, workspace_id: str | None, project: CodexProjectTarget | None):
        if project is not None:
            workspace = None
        elif workspace_id is None:
            workspace = self.host.home.root / "codex_workspaces" / uuid4().hex
            workspace.mkdir(parents=True)
        else:
            workspace = self.host.workspaces.get(workspace_id).task_root
        return {"identity": await prepare_codex_chat(self.codex_link, workspace, project=project)}

    async def start_local_codex(self, identity, text):
        if self.has_local_codex(identity):
            return await self.read_local_codex(identity)
        history = await finish_on_cancel(send_codex_chat(
            self.codex_link, identity, text, accept_first=partial(self.bind_local_codex, identity),
        ))
        await self.notify_changed()
        return history

    async def bind_local_codex(self, identity):
        async with self._changed:
            session = next((item for item in self.sessions.read().sessions if item.identity == identity), None)
            if session is None:
                self.sessions.add(ExternalSession(identity=identity, created_at=datetime.now(UTC), local_codex=True))
            elif not session.local_codex:
                self.sessions.update(session.model_copy(update={"local_codex": True}))
            else:
                return {"identity": identity}
        await self.notify_changed()
        return {"identity": identity}

    async def list_local_codex(self, query):
        return await list_codex_chats(self.codex_link, query)

    async def local_codex_project(self, project_id):
        return await read_codex_project(await self.codex_link.require(), project_id)

    async def delegate_a2a(self, connection, identity, work):
        from konden.assistant.host.a2a_to_bot import delegate_to_konden

        if self._session(connection.connection_id, identity) is None:
            return self.not_found(), []
        return await delegate_to_konden(self, connection, identity, work)

    async def import_local_codex(self, identity):
        await native_codex_thread(await self.codex_link.require(), identity)
        return await self.bind_local_codex(identity)

    async def codex_choices(self):
        identities = [item.identity for item in self.sessions.read().sessions if item.local_codex]
        if not identities:
            return []
        try:
            server = await self.codex_link.require()
        except CodexDisconnected:
            server = None
        titles = await read_codex_titles(identities, server=server)
        return [{"identity": identity, "name": titles.get(identity, external_name(identity))} for identity in identities]

    def has_local_codex(self, identity):
        return any(item.identity == identity and item.local_codex for item in self.sessions.read().sessions)

    def _require_local_codex(self, identity):
        if not self.has_local_codex(identity):
            raise ExternalSessionNotFound(identity)

    async def read_local_codex(self, identity):
        self._require_local_codex(identity)
        return await read_codex_chat(self.codex_link, identity)

    async def send_local_codex(self, identity, text):
        self._require_local_codex(identity)
        history = await finish_on_cancel(send_codex_chat(self.codex_link, identity, text))
        await self.notify_changed()
        return history

    def _active(self, connection_id):
        return any(item.connection_id == connection_id for item in self.connections.read().connections)

    def _session(self, connection_id, identity):
        return next((item for item in self.sessions.read().sessions
                     if item.connection_id == connection_id and item.identity == identity), None)

    async def messages(self, session_id):
        return [(event, parse_external(event.payload))
                for event in await self.host.store.journal(session_id).snapshot(session_id)
                if isinstance(event.payload, DomainFactCommitted) and event.payload.fact_type == EXTERNAL_MESSAGE]

    async def recover_user_inputs(self):
        """Complete accepted human inputs before any Bot Worker is resumed."""
        restored = set()
        for agent in await self.host.agents.list(include_deleted=True):
            if agent.deleted:
                continue
            journal = self.host.store.journal(agent.session_id)
            events = await journal.snapshot(agent.session_id)
            received = {event.delivery.delivery_id for event in events
                        if isinstance(event.payload, UserMessageReceived) and event.delivery is not None
                        and event.delivery.source == USER_SOURCE}
            for event in events:
                if not isinstance(event.payload, DomainFactCommitted) or event.payload.fact_type != EXTERNAL_MESSAGE:
                    continue
                message = parse_external(event.payload)
                if message.user_input and message.message_id not in received:
                    await AgentRuntime(journal, None, {}).receive_user_message(agent.session_id,
                        message.user_content(), delivery_id=message.message_id, source=USER_SOURCE,
                        **({"artifact_refs": tuple(message.artifact_refs)} if message.artifact_refs else {}))
                    restored.add(agent.session_id)
        return restored

    async def _refresh(self):
        agents = {agent.session_id: agent for agent in await self.host.agents.list(include_deleted=True)}
        for session_id in agents:
            events = await self.host.store.journal(session_id).snapshot(session_id)
            position = self._positions.get(session_id, 0)
            additions = [(event, parse_external(event.payload)) for event in events[position:]
                         if isinstance(event.payload, DomainFactCommitted)
                         and event.payload.fact_type == EXTERNAL_MESSAGE]
            for event, message in additions:
                messages = self._conversations.setdefault(message.identity, [])
                insort_right(messages, (session_id, event, message), key=lambda item: item[2].sequence)
            self._positions[session_id] = max(position, len(events))
        return agents

    def _messages_for(self, session, agents):
        return [(agents[session_id], event, message) for session_id, event, message
                in self._conversations.get(session.identity, ())]

    async def _conversation(self, session):
        return self._messages_for(session, await self._refresh())

    async def _names(self, sessions):
        titles = await read_codex_titles([session.identity for session in sessions if session.local_codex or session.label is None],
                                        server=self.codex_link.current)
        return {session.identity: titles.get(session.identity, external_name(session.identity) if session.local_codex else session.name)
                for session in sessions}

    def _session_view(self, session, messages, active_connections, bindings, names):
        peers = {agent.session_id: {"name": agent.name, "session_id": agent.session_id, "deleted": agent.deleted}
                 for agent, _, _ in messages}
        latest = next((message.message_id for _, _, message in reversed(messages) if message.calls_codex), None)
        return {"identity": session.identity, "name": names[session.identity],
                "created_at": session.created_at.isoformat(),
                "last_activity_at": max([session.created_at, *(event.occurred_at for _, event, _ in messages)]).isoformat(),
                "active": session.connection_id in active_connections, "peers": list(peers.values()),
                "codex_status": continuation_status(bindings.get(session.identity), latest),
                **({"local_codex": True} if session.local_codex else {})}

    async def list_sessions(self):
        sessions = self.sessions.read().sessions
        if not sessions:
            return []
        agents = await self._refresh()
        active = {item.connection_id for item in self.connections.read().connections}
        bindings = self.codex.read().bindings
        names = await self._names(sessions)
        return sorted((self._session_view(session, self._messages_for(session, agents), active, bindings, names)
                       for session in sessions),
                      key=lambda item: item["last_activity_at"], reverse=True)

    async def view(self, identity):
        session = next((item for item in self.sessions.read().sessions if item.identity == identity), None)
        if session is None:
            raise ExternalSessionNotFound(identity)
        messages = await self._conversation(session)
        active = {item.connection_id for item in self.connections.read().connections}
        latest = next((message.message_id for _, _, message in reversed(messages) if message.calls_codex), None)
        names = await self._names([session])
        return {"session": self._session_view(session, messages, active, self.codex.read().bindings, names),
                "codex": self.codex.view(identity, latest), "messages": [
            {"message_id": message.message_id, "sequence": message.sequence,
             "direction": message.direction, "sender": message.sender, "recipients": list(message.recipients),
             "text": message.text, "reply_to": message.reply_to,
             "attachments": describe_attachments(self._store(agent.session_id), message.artifact_refs),
             "bot_name": agent.name, "bot_session_id": agent.session_id,
             "occurred_at": event.occurred_at.isoformat()} for agent, event, message in messages]}

    async def configure_codex(self, identity, target):
        session = next((item for item in self.sessions.read().sessions if item.identity == identity), None)
        if session is None:
            raise ExternalSessionNotFound(identity)
        if target is not None and not self._active(session.connection_id):
            raise CodexContinuationInvalid("接入已撤销，不能启用自动接续。")
        self.codex.configure(identity, target)
        await self.notify_changed()
        return await self.view(identity)

    async def retry_codex(self, identity):
        session = next((item for item in self.sessions.read().sessions if item.identity == identity), None)
        if session is None:
            raise ExternalSessionNotFound(identity)
        if not self._active(session.connection_id):
            raise CodexContinuationInvalid("接入已撤销，不能提醒 Codex。")
        if identity not in self.codex.read().bindings:
            raise CodexContinuationInvalid("请先启用 Codex 自动接续。")
        messages = await self._conversation(session)
        latest = next((message.message_id for _, _, message in reversed(messages) if message.calls_codex), None)
        if latest is None:
            raise CodexContinuationInvalid("还没有需要 Codex 处理的消息。")
        await self.codex.notify(identity, latest, retry=True)
        await self.notify_changed()
        return await self.view(identity)

    async def colleagues(self, session_id):
        if await self.host.agents.by_session(session_id) is None:
            raise ValueError("external conversation query requires a Bot identity")
        sessions = await self.list_sessions()
        return {"ok": True, "code": "EXTERNAL_SESSIONS_READ", "data": {"sessions": [
            {key: item[key] for key in ("identity", "name", "active", "last_activity_at", "codex_status")}
            for item in sessions if item["active"] or not item.get("local_codex")]}}

    async def context(self, connection, identity, name=None):
        try:
            server = await self.codex_link.require()
        except CodexDisconnected:
            # The external conversation still has its configured tools while native access is offline.
            server = None
        threads = await read_codex_threads([identity], server=server)
        local = identity in threads
        async with self._changed:
            if not self._active(connection.connection_id):
                return self.revoked()
            session = next((item for item in self.sessions.read().sessions if item.identity == identity), None)
            use_alias = name is not None and not local and (session is None or not session.local_codex)
            if session is None:
                session = ExternalSession(connection_id=connection.connection_id, identity=identity,
                                          created_at=datetime.now(UTC), label=name if use_alias else None, local_codex=local)
                self.sessions.add(session)
                await self.notify_changed()
            elif (session.connection_id != connection.connection_id or (use_alias and name != session.name)
                  or (local and not session.local_codex)):
                if session.connection_id != connection.connection_id and self._active(session.connection_id):
                    return {"ok": False, "code": "CONNECTION_CONFLICT",
                            "error": "当前聊天已使用另一份接入配置。请先在 Web 撤销原配置，再通过新配置连接。"}
                session = session.model_copy(update={"connection_id": connection.connection_id,
                    "label": name if use_alias else session.label, "local_codex": session.local_codex or local})
                self.sessions.update(session)
                await self.notify_changed()
        names = await self._names([session])
        return {"ok": True, "identity": session.identity, "name": names[session.identity],
                "colleagues": [{"name": item.name} for item in await self.host.agents.list()],
                "notice": ("已自动识别当前 Codex 聊天，继续或重连无需保存和传回身份。"
                           "用 delegate_a2a_task 派单，用 send_message 与同事交谈，用 read_messages 收取消息。")}

    @staticmethod
    def _valid_reply(messages, session_id, reply_to):
        return reply_to is None or any(agent.session_id == session_id and message.message_id == reply_to
                                      for agent, _, message in messages)

    @staticmethod
    def _target(messages, reply_to, explicit, counterpart):
        if explicit is not None:
            return explicit
        original = next((message for _, _, message in messages if message.message_id == reply_to), None)
        return "user" if original is not None and original.sender == "user" else counterpart

    async def post_user(self, identity, arguments):
        message_id = "external-user-" + sha256(json.dumps(
            [identity, arguments.bot_session_id, arguments.delivery_id]).encode()).hexdigest()
        async with self._changed:
            session = next((item for item in self.sessions.read().sessions if item.identity == identity), None)
            if session is None:
                raise ExternalSessionNotFound(identity)
            if not self._active(session.connection_id):
                raise ExternalMessageInvalid("接入已撤销，不能继续发送。")
            bot = await self.host.agents.by_session(arguments.bot_session_id)
            if bot is None or bot.deleted:
                raise ExternalMessageInvalid("这位 Bot 已不可用，请重新选择。")
            messages = await self._conversation(session)
            if not self._valid_reply(messages, bot.session_id, arguments.reply_to):
                raise ExternalMessageInvalid("只能引用这段三方对话中的消息。")
            direction = external_direction("user", arguments.target)
            existing = next((message for _, _, message in messages if message.message_id == message_id), None)
            if existing is not None:
                if (existing.text != arguments.text or existing.direction != direction
                        or existing.reply_to != arguments.reply_to or existing.artifact_refs != arguments.artifact_refs):
                    raise ExternalMessageInvalid("该消息编号已用于不同内容，请作为新消息发送。")
                message = existing
            else:
                gateway = AttachmentGateway(self.host.store.root)
                for ref in arguments.artifact_refs:
                    if not await gateway.available_in_session(bot.session_id, ref):
                        raise AttachmentRejected("附件不属于这位 Bot 的会话。")
                    if not self._store(bot.session_id).path(ref).is_file():
                        raise FileNotFoundError(self._store(bot.session_id).path(ref))
                message = ExternalMessage(connection_id=session.connection_id, identity=identity, name="用户",
                    message_id=message_id, sequence=len(messages) + 1, direction=direction,
                    reply_to=arguments.reply_to, text=arguments.text, artifact_refs=arguments.artifact_refs)
                if message.user_input and self.host.models is not None:
                    self.host.models.require_ready(bot.session_id)
                await self.host.request("fact", bot.session_id, message.arguments())
            self._changed.notify_all()
            await self.notify_changed()
        if message.user_input:
            await self.host.receive_user_message(bot.session_id, message.user_content(),
                delivery_id=message_id, source=USER_SOURCE,
                **({"artifact_refs": tuple(message.artifact_refs)} if message.artifact_refs else {}))
        if message.calls_codex and await self.codex.notify(identity, message_id):
            await self.notify_changed()
        return await self.view(identity)

    async def _share_was_committed(self, session_id, arguments):
        events = await self.host.store.journal(session_id).snapshot(session_id)
        accepted = next((event for event in events if event.delivery is not None
                         and event.delivery.source == arguments["source"]
                         and event.delivery.delivery_id == arguments["delivery_id"]), None)
        if accepted is None:
            return False
        if accepted.payload != DomainFactCommitted(arguments["fact_type"], arguments["data"]):
            raise ValueError("accepted external message conflicts with its snapshot")
        return True

    async def _commit_share(self, session_id, root, message):
        if not message.attachment_paths:
            await self.host.request("fact", session_id, message.arguments())
            return message

        store = self._store(session_id)
        writes = AttachmentSnapshot()
        arguments = None
        try:
            refs = await finish_on_cancel(asyncio.to_thread(snapshot_files, store, root, message.attachment_paths, writes))
            message = message.model_copy(update={"artifact_refs": refs})
            arguments = message.arguments()
            await self.host.request("fact", session_id, arguments, settle_dispatched=True)
        except BaseException as error:
            try:
                # IPC may fail after the Worker committed. A failed journal read
                # leaves ownership unknown, so keep the bytes and surface both errors.
                if arguments is not None and await self._share_was_committed(session_id, arguments):
                    writes.publish()
                else:
                    writes.discard()
            except BaseException as cleanup_error:
                raise BaseExceptionGroup("attachment share and reconciliation failed", [error, cleanup_error]) from cleanup_error
            raise
        writes.publish()
        return message

    async def send(self, connection, identity, arguments, *, a2a_task_id=None):
        message_id = "external-" + sha256(json.dumps(
            [connection.connection_id, identity, arguments.delivery_id]).encode()).hexdigest()
        observed = self._session(connection.connection_id, identity)
        names = await self._names([observed]) if observed is not None else {}
        async with self._changed:
            if not self._active(connection.connection_id):
                return self.revoked()
            session = self._session(connection.connection_id, identity)
            if session is None:
                return self.not_found()
            messages = await self._conversation(session)
            direction = external_direction("codex", self._target(messages, arguments.reply_to, arguments.target, "bot"))
            existing = next((item for item in messages if item[2].message_id == message_id), None)
            if existing is not None:
                agent, _, message = existing
                if (arguments.recipient not in agent.names or message.text != arguments.text
                        or message.reply_to != arguments.reply_to or message.direction != direction
                        or message.attachment_paths != arguments.files):
                    return {"ok": False, "code": "DELIVERY_CONFLICT",
                            "error": "delivery_id 已用于另一条消息；重试保持内容不变，新消息使用新标识。"}
                if message.a2a_task_id != a2a_task_id:
                    return {"ok": False, "code": "A2A_TASK_ALREADY_SENT" if message.a2a_task_id else "DELIVERY_CONFLICT",
                            "error": "这条派单已发送，请读取原任务的答复。" if message.a2a_task_id else "该标识已用于普通消息。",
                            "message_id": message_id, "task_id": message.a2a_task_id}
                return self.delivered(message_id, agent.name)
            try:
                recipient = await self.host.agents.resolve(arguments.recipient)
            except AgentNotFound:
                return {"ok": False, "code": "RECIPIENT_NOT_FOUND", "error": "没有这位同事，请用 get_context 查询当前名字。"}
            if not self._valid_reply(messages, recipient.session_id, arguments.reply_to):
                return self.invalid_reply()
            root = None
            if arguments.files:
                thread = (await read_codex_threads([identity], server=self.codex_link.current)).get(identity)
                if thread is None or thread.cwd is None or not Path(thread.cwd).is_absolute():
                    return {"ok": False, "code": "FILE_CONTEXT_UNAVAILABLE",
                            "error": "无法确认当前 Codex 聊天的工作目录，请在本机 Desktop 打开该聊天后重试。"}
                root = Path(thread.cwd)
            message = ExternalMessage(connection_id=connection.connection_id, identity=session.identity,
                name=names.get(session.identity, session.name), message_id=message_id, sequence=len(messages) + 1, direction=direction,
                reply_to=arguments.reply_to, text=arguments.text, attachment_paths=arguments.files, a2a_task_id=a2a_task_id)
            try:
                await self._commit_share(recipient.session_id, root, message)
            except AttachmentRejected as error:
                return {"ok": False, "code": "ATTACHMENT_REJECTED", "error": str(error)}
            except AgentDeletedError:
                return {"ok": False, "code": "RECIPIENT_NOT_FOUND", "error": "这位同事已退出。"}
            await self.notify_changed()
            return self.delivered(message_id, recipient.name)

    async def await_a2a_reply(self, connection, identity, message_id):
        async with self._changed:
            while True:
                session = self._session(connection.connection_id, identity)
                if session is None or not self._active(connection.connection_id):
                    return None
                messages = await self._conversation(session)
                for agent, _, message in messages:
                    if message.reply_to == message_id and message.task_result is not None:
                        return agent.session_id, message
                await self._changed.wait()

    async def _read(self, connection, identity, after, limit):
        if not self._active(connection.connection_id):
            return self.revoked()
        session = self._session(connection.connection_id, identity)
        if session is None:
            return self.not_found()
        agents = await self._refresh()
        messages = self._conversations.get(session.identity, ())
        if after > len(messages):
            return {"ok": False, "code": "INVALID_CURSOR", "error": "请使用这个会话上次返回的 next_cursor。"}
        position = bisect_right(messages, after, key=lambda item: item[2].sequence)
        received = (item for item in islice(messages, position, None) if item[2].sender != "codex")
        page = list(islice(received, limit + 1))
        has_more = len(page) > limit
        page = page[:limit]
        return {"ok": True, "messages": [
            {"message_id": message.message_id, "sender": {"kind": message.sender, "name": message.name},
             "bot_name": agents[session_id].name, "recipients": list(message.recipients),
             "response_requested": message.calls_codex,
             "attachments": describe_attachments(self._store(session_id), message.artifact_refs),
             "text": message.text, "reply_to": message.reply_to, "occurred_at": event.occurred_at.isoformat(),
             **({"task_result": message.task_result} if message.task_result is not None else {})}
            for session_id, event, message in page],
            "next_cursor": page[-1][2].sequence if page else after, "has_more": has_more,
            "notice": "空结果只表示目前没有新消息。保留 next_cursor，继续读取当前聊天的后续消息。"}

    async def read(self, connection, identity, after, limit, wait_seconds):
        deadline = asyncio.get_running_loop().time() + wait_seconds
        async with self._changed:
            while True:
                result = await self._read(connection, identity, after, limit)
                remaining = deadline - asyncio.get_running_loop().time()
                if not result["ok"] or result["messages"] or remaining <= 0:
                    return result
                try:
                    await asyncio.wait_for(self._changed.wait(), remaining)
                except TimeoutError:
                    return await self._read(connection, identity, after, limit)

    async def read_attachment(self, connection, identity, message_id, attachment_id):
        if not self._active(connection.connection_id):
            return self.revoked()
        session = self._session(connection.connection_id, identity)
        if session is None:
            return self.not_found()
        messages = await self._conversation(session)
        found = next(((agent, message) for agent, _, message in messages
                      if message.message_id == message_id and attachment_id in message.artifact_refs), None)
        if found is None:
            return {"ok": False, "code": "ATTACHMENT_NOT_SHARED",
                    "error": "只能读取当前聊天共享消息中明确附带的附件，请使用消息中的 message_id 和 attachment_id。"}
        agent, _ = found
        store = self._store(agent.session_id)
        attachment, = describe_attachments(store, [attachment_id])
        path = store.files.materialize(attachment_id) if is_file_attachment_id(attachment_id) else store.path(attachment_id)
        return {"ok": True, "attachment": attachment, "path": str(path),
                "notice": "这是消息附带的材料；普通文件可按 path 读取。文件只读，编辑前先复制到你的工作目录。"}

    async def send_from_bot(self, session_id, arguments):
        async with self._changed:
            session = next((item for item in self.sessions.read().sessions
                            if item.identity == arguments["identity"]), None)
            if session is None:
                return self.not_found()
            messages = await self._conversation(session)
            direction = external_direction("bot", self._target(messages, arguments["reply_to"], arguments["target"], "codex"))
            task_result = arguments.get("task_result")
            parent = next((item for agent, _, item in messages if agent.session_id == session_id
                           and item.message_id == arguments["reply_to"]), None)
            a2a_reply = parent is not None and parent.a2a_task_id is not None
            message_id = message_identity(session_id, arguments["command_id"])
            existing = next((message for _, _, message in messages if message.message_id == message_id), None)
            if existing is not None:
                if (existing.text != arguments["text"] or existing.reply_to != arguments["reply_to"]
                        or existing.direction != direction or existing.attachment_paths != arguments["files"]
                        or existing.task_result != task_result):
                    raise ValueError("persisted external message conflicts with its Command")
                message = existing
            else:
                if not self._active(session.connection_id):
                    return self.revoked()
                if arguments["reply_to"] is not None and parent is None:
                    return self.invalid_reply()
                if task_result is not None:
                    if not a2a_reply or direction == "bot_user":
                        return {"ok": False, "code": "INVALID_A2A_REPLY", "error": "请引用收到的 A2A 委派，并把交付发给 Codex。"}
                    if any(item.reply_to == parent.message_id and item.task_result is not None for _, _, item in messages):
                        return {"ok": False, "code": "A2A_TASK_REPLIED", "error": "这项 A2A 委派已有交付结果。"}
                sender = await self.host.agents.by_session(session_id)
                if sender is None:
                    raise ValueError("external message sender requires a Bot identity")
                message = ExternalMessage(connection_id=session.connection_id, identity=session.identity,
                    name=sender.name, message_id=message_id, sequence=len(messages) + 1, direction=direction,
                    reply_to=arguments["reply_to"], text=arguments["text"], attachment_paths=arguments["files"],
                    task_result=task_result)
                try:
                    message = await self._commit_share(session_id, sender.directory, message)
                except AttachmentRejected as error:
                    return {"ok": False, "code": "ATTACHMENT_REJECTED", "error": str(error)}
            self._changed.notify_all()
            await self.notify_changed()
        if message.calls_codex and not a2a_reply and await self.codex.notify(session.identity, message_id):
            await self.notify_changed()
        return self.delivered(message_id, session.name)

    @staticmethod
    def delivered(message_id, recipient):
        return {"ok": True, "code": "DELIVERED", "message_id": message_id, "recipient": recipient,
                "notice": "消息已送达，对方可以读取；不表示对方已经处理。"}

    @staticmethod
    def not_found():
        return {"ok": False, "code": "SESSION_NOT_FOUND",
                "error": ("当前外部会话尚未接入或已被用户删除，不能收发。请告知用户；"
                          "用户要求重新接入时，原生聊天可调用 get_context。Bot 可用 list_external_sessions 查询其他已接入会话。")}

    @staticmethod
    def invalid_reply():
        return {"ok": False, "code": "INVALID_REPLY", "error": "reply_to 必须引用你与这位对话对象之间的消息。"}

    @staticmethod
    def revoked():
        return {"ok": False, "code": "CONNECTION_REVOKED", "error": "用户已撤销接入，不能继续收发。"}
