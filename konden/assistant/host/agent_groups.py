"""Host-owned group records and deterministic turn scheduling for group chats."""
from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from konden.assistant.agent_messages import (
    MAX_GROUP_REPLIES,
    MAX_GROUP_ROUNDS,
)
from konden.assistant.agents import (
    GROUP_CREATED,
    GROUP_DISSOLVED,
    GROUP_MESSAGE,
    GroupAttention,
    GroupCreated,
    GroupDissolved,
    GroupMessage,
    Speaker,
    fact_arguments,
    parse_agent_fact,
)
from konden.assistant.host.agent_roster import AgentDeletedError, AgentNotFound
from konden.assistant.host.session_store import SessionStore
from konden.assistant.runner import SessionNotFoundError
from konden.runtime import AgentRuntime, DomainFactCommitted
from konden.runtime.json_values import thaw_value

_DRIVE = "group.drive"
_FAVORITE = "group.favorite"


class GroupNameTaken(ValueError):
    pass


class GroupNotFound(LookupError):
    pass


class GroupDissolvedError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class GroupRecord:
    group_id: str
    created: GroupCreated
    member_sessions: dict[str, str]
    last_activity: datetime
    favorite: bool
    dissolved: bool = False

    @property
    def name(self) -> str:
        return self.created.group

    @property
    def members(self) -> list[str]:
        """Names at creation remain the keys of already accepted group turns."""
        return self.created.members

    def summary(self, current_members: list[str]) -> dict:
        return {"group_id": self.group_id, "name": self.name, "members": current_members,
                "favorite": self.favorite, "last_activity": self.last_activity.isoformat()}


def _targets(group: GroupRecord, speaker: str | None, mentioned: list[str]) -> list[str]:
    return [name for name in (mentioned or group.members) if name != speaker]


@dataclass(frozen=True, slots=True)
class _Call:
    """A member asked to answer a message, in a given round of the discussion that message belongs to."""

    message_id: str
    opener: str
    round: int


@dataclass(eq=False)
class _Drive:
    """Who the Host calls in one group, until a person speaks there again.

    A speech that quotes a message its sender was called to answer continues that
    call's discussion. Any other speech opens a discussion of its own, with its own
    round and reply budget, and joins the queue instead of replacing it.
    """

    group: GroupRecord
    replies: Counter = field(default_factory=Counter)
    pending: dict[str, _Call] = field(default_factory=dict)
    batch: dict[str, _Call] = field(default_factory=dict)
    active: str | None = None
    outbox: dict[str, GroupMessage] = field(default_factory=dict)
    # Calls not yet answered, by member and the message it was asked to answer.
    answering: dict[tuple[str, str], _Call] = field(default_factory=dict)
    # Set when a person's new message replaces this drive.
    replaced: asyncio.Event = field(default_factory=asyncio.Event)
    running: bool = False

    def queue(self, speaker: str | None, mentioned: list[str], call: _Call) -> bool:
        """Queue whoever this speech calls; False when its discussion has no round left."""
        if call.round > MAX_GROUP_ROUNDS:
            return False
        for name in _targets(self.group, speaker, mentioned):
            self.pending.setdefault(name, call)
        return True


class AgentGroups:
    def __init__(self, host):
        self.host = host
        self.store = SessionStore(host.home.state_root / "groups")
        self._creation_lock = asyncio.Lock()
        self._group_locks: dict[str, asyncio.Lock] = {}
        self._drives: dict[str, _Drive] = {}

    async def _load(self, group: GroupRecord) -> _Drive:
        if group.group_id in self._drives:
            return self._drives[group.group_id]
        drive = _Drive(group)
        events = await self._journal(group.group_id).snapshot(group.group_id)
        saved = [event.payload.data for event in events if isinstance(event.payload, DomainFactCommitted)
                 and event.payload.fact_type == _DRIVE]
        if saved:
            state = thaw_value(saved[-1])
            drive.replies = Counter(state["replies"])
            drive.pending = {name: _Call(**call) for name, call in state["pending"]}
            drive.batch = {name: _Call(**call) for name, call in state["batch"]}
            drive.active = state["active"]
            drive.answering = {(name, call["message_id"]): _Call(**call) for name, call in state["answering"]}
            drive.outbox = {item["message_id"]: GroupMessage.model_validate(item) for item in state["outbox"]}
        self._drives[group.group_id] = drive
        return drive

    async def _save(self, drive: _Drive, *, receipt: tuple[str, bool] | None = None) -> None:
        # One durable group fact accepts both the speech and its scheduling effects.
        # Public/member copies can then be completed using their original delivery id.
        data = {"replies": dict(drive.replies), "pending": [(name, asdict(call)) for name, call in drive.pending.items()],
                    "batch": [(name, asdict(call)) for name, call in drive.batch.items()], "active": drive.active,
                    "answering": [(name, asdict(call)) for (name, _), call in drive.answering.items()],
                    "outbox": [message.model_dump(mode="json", exclude_defaults=True) for message in drive.outbox.values()],
                    "receipt": None if receipt is None else list(receipt)}
        await AgentRuntime(self._journal(drive.group.group_id), None, {}).receive_domain_fact(
            drive.group.group_id, _DRIVE, data, source="group", delivery_id=uuid4().hex)

    async def _flush(self, drive: _Drive) -> None:
        for identity, message in list(drive.outbox.items()):
            await self._publish(drive.group, message)
            del drive.outbox[identity]
            await self._save(drive)

    async def prepare_recovery(self) -> None:
        # Publish all accepted source records before any Worker can rebuild origin budgets.
        for group in await self.list():
            drive = await self._load(group)
            for message in drive.outbox.values():
                await AgentRuntime(self._journal(group.group_id), None, {}).receive_domain_fact(
                    group.group_id, **fact_arguments(message, delivery_id=message.message_id))

    async def recover(self) -> None:
        await self.prepare_recovery()
        for group in await self.list(include_dissolved=True):
            async with self._group_locks.setdefault(group.group_id, asyncio.Lock()):
                await self._notify_membership(group)
                if group.dissolved:
                    continue
                drive = self._drives[group.group_id]
                await self._flush(drive)
                self._schedule(drive)

    async def _receipt(self, group_id: str, identity: str) -> bool | None:
        for event in await self._journal(group_id).snapshot(group_id):
            if isinstance(event.payload, DomainFactCommitted) and event.payload.fact_type == _DRIVE:
                receipt = event.payload.data["receipt"]
                if receipt is not None and receipt[0] == identity:
                    return receipt[1]
        return None

    def _journal(self, group_id):
        try:
            return self.store.journal(group_id)
        except SessionNotFoundError as error:
            raise GroupNotFound(group_id) from error

    async def _record(self, group_id):
        events = await self._journal(group_id).snapshot(group_id)
        facts = [event for event in events if isinstance(event.payload, DomainFactCommitted)]
        creation, = (event for event in facts if event.payload.fact_type == GROUP_CREATED)
        created = parse_agent_fact(creation.payload)
        # Groups predating temporary discussions keep their existing sidebar entry.
        favorite = next((event.payload.data["favorite"] for event in reversed(facts)
                         if event.payload.fact_type == _FAVORITE), True)
        activity = next(event.occurred_at for event in reversed(facts)
                        if event.payload.fact_type in (GROUP_CREATED, GROUP_MESSAGE))
        dissolved = any(parse_agent_fact(event.payload) for event in facts if event.payload.fact_type == GROUP_DISSOLVED)
        sessions = created.member_sessions
        if sessions is None:
            sessions = {name: (await self.host.agents.original_owner(name)).session_id for name in created.members}
        return GroupRecord(group_id, created, sessions, activity, favorite, dissolved), events

    async def list(self, *, include_dissolved=False) -> tuple[GroupRecord, ...]:
        groups = []
        for group_id in await self.store.identities():
            group, _events = await self._record(group_id)
            if include_dissolved or not group.dissolved:
                groups.append(group)
        return tuple(sorted(groups, key=lambda group: group.name))

    async def get(self, group_id: str) -> GroupRecord:
        group, _events = await self._record(group_id)
        return group

    async def view(self, group_id: str) -> dict:
        """What a person sees: the public record and who the Host is calling now."""
        group, events = await self._record(group_id)
        drive = self._drives.get(group_id)
        active = await self._active_members(group)
        return {
            **group.summary(list(active)),
            "dissolved": group.dissolved,
            "member_sessions": {name: group.member_sessions[key] for name, key in active.items()},
            "calling": [(await self.host.agents.by_session(group.member_sessions[drive.active])).name]
                       if drive is not None and drive.running and drive.active is not None and not group.dissolved else [],
            "messages": [
                {"message_id": fact.message_id, "sender": fact.sender.name,
                 "sender_session_id": await self.host.agents.sender_session(fact), "text": fact.text,
                 "mentioned_agents": fact.mentioned_agents, "reply_to": fact.reply_to,
                 "occurred_at": event.occurred_at.isoformat()}
                for event in events
                if isinstance(event.payload, DomainFactCommitted) and event.payload.fact_type == GROUP_MESSAGE
                for fact in (parse_agent_fact(event.payload),)
            ],
        }

    async def messages(self, group_id: str) -> list[GroupMessage]:
        return [parse_agent_fact(event.payload) for event in await self._journal(group_id).snapshot(group_id)
                if isinstance(event.payload, DomainFactCommitted) and event.payload.fact_type == GROUP_MESSAGE]

    async def _active_members(self, group):
        members = {}
        for key, session_id in group.member_sessions.items():
            agent = await self.host.agents.by_session(session_id)
            if not agent.deleted:
                members[agent.name] = key
        return members

    async def member_names(self, group: GroupRecord) -> list[str]:
        return list(await self._active_members(group))

    async def create(self, *, name: str, members: list[str], favorite: bool = False) -> GroupRecord:
        group_id = f"group-{uuid4().hex}"
        return await self._create(group_id, GroupCreated(group=name, members=members, group_path=f"/groups/{group_id}"), favorite)

    async def create_from_bot(self, session_id: str, arguments: dict) -> dict:
        sender = await self.host.agents.by_session(session_id)
        if sender is None:
            raise ValueError("group creation requires a Bot identity")
        colleagues = arguments["colleagues"]
        if sender.name in colleagues:
            return {"ok": False, "code": "INVALID_ARGUMENT", "error": "你自动入群，只填写要邀请的同事。"}
        identity = uuid5(NAMESPACE_URL, f"konden:group:{session_id}\0{arguments['command_id']}")
        group_id = f"group-{identity.hex}"
        created = GroupCreated(group=arguments["name"], members=[sender.name, *colleagues],
                               started_by=sender.name, group_path=f"/groups/{group_id}")
        try:
            group = await self._create(group_id, created, arguments["favorite"], starter=sender)
        except GroupNameTaken as error:
            return {"ok": False, "code": "GROUP_NAME_TAKEN", "error": str(error)}
        except AgentNotFound as error:
            return {"ok": False, "code": "RECIPIENT_NOT_FOUND", "error": f"没有这位同事：{error.args[0]}。请查看 list_colleagues。"}
        return {"ok": True, "code": "GROUP_CREATED", "data": {"group": group.name,
                "group_path": f"/groups/{group.group_id}", "members": await self.member_names(group),
                "dissolved": group.dissolved}}

    async def _create(self, group_id: str, created: GroupCreated, favorite: bool, *, starter=None) -> GroupRecord:
        async with self._creation_lock:
            existing = next((g for g in await self.list(include_dissolved=True)
                             if g.group_id == group_id or g.name == created.group), None)
            if existing is None:
                sessions = {starter.name: starter.session_id} if starter is not None else {}
                for name in created.members:
                    if name not in sessions:
                        sessions[name] = (await self.host.agents.resolve(name)).session_id
                created = created.model_copy(update={"member_sessions": sessions})
                await self.store.create(group_id, initial_fact=fact_arguments(created, delivery_id="created"),
                    additional_facts=({"fact_type": _FAVORITE, "data": {"favorite": favorite}, "source": "group",
                                           "delivery_id": "initial", "requests_decision": False},))
            elif existing.group_id != group_id:
                raise GroupNameTaken(f"已有群：{created.group}")
            elif existing.name != created.group or existing.members[1:] != created.members[1:]:
                raise ValueError("group creation identity conflicts with its content")
        async with self._group_locks.setdefault(group_id, asyncio.Lock()):
            group = await self.get(group_id)
            if not group.dissolved:
                await self._notify_membership(group)
        self._changed(group_id)
        return group

    async def set_favorite(self, group_id: str, favorite: bool) -> None:
        async with self._group_locks.setdefault(group_id, asyncio.Lock()):
            group = await self.get(group_id)
            if group.dissolved:
                raise GroupDissolvedError(group_id)
            if group.favorite != favorite:
                await AgentRuntime(self._journal(group_id), None, {}).receive_domain_fact(
                    group_id, _FAVORITE, {"favorite": favorite}, source="group", delivery_id=uuid4().hex)
                self._changed(group_id)

    async def _publish(self, group: GroupRecord, message: GroupMessage) -> None:
        """The group record is the source; member timelines receive copies without deciding."""
        arguments = fact_arguments(message, delivery_id=message.message_id)
        await AgentRuntime(self._journal(group.group_id), None, {}).receive_domain_fact(group.group_id, **arguments)
        sender_id = await self.host.agents.sender_session(message)
        recipients = [session_id for session_id in group.member_sessions.values() if session_id != sender_id
                      and not (await self.host.agents.by_session(session_id)).deleted]
        await asyncio.gather(*(self._deliver_member(session_id, arguments) for session_id in recipients))
        self._changed(group.group_id)

    async def _deliver_member(self, session_id, arguments):
        try:
            await self.host.request("fact", session_id, arguments)
        except AgentDeletedError:
            # The person may delete a member while this accepted message is being copied.
            return

    async def _notify_membership(self, group: GroupRecord) -> None:
        fact = GroupDissolved(group=group.name) if group.dissolved else group.created
        action = "dissolved" if group.dissolved else "joined"
        arguments = fact_arguments(fact, delivery_id=f"{action}:{group.group_id}")
        members = [await self.host.agents.by_session(session_id) for session_id in group.member_sessions.values()]
        await asyncio.gather(*(self._deliver_member(member.session_id, arguments)
                               for member in members if not member.deleted))

    async def dissolve(self, group_id: str) -> None:
        async with self._group_locks.setdefault(group_id, asyncio.Lock()):
            group = await self.get(group_id)
            if not group.dissolved:
                drive = await self._load(group)
                await self._flush(drive)
                await AgentRuntime(self._journal(group_id), None, {}).receive_domain_fact(group_id,
                    **fact_arguments(GroupDissolved(group=group.name), delivery_id="dissolved"))
                drive.replaced.set()
            await self._notify_membership(replace(group, dissolved=True))
            self._changed(group_id)

    def _changed(self, group_id: str) -> None:
        """Tell observers the record or who is being called changed; never delays scheduling."""
        if self.host.group_changed_sink is not None:
            self.host._track(group_id, asyncio.create_task(self._notify(group_id)))

    async def _notify(self, group_id: str) -> None:
        observed = self.host.group_changed_sink(group_id)
        if isinstance(observed, Awaitable):
            await observed

    async def post(self, group_id: str, text: str, mention: list[str], *, delivery_id: str | None = None) -> GroupMessage:
        """A human message starts a new drive, superseding the previous one."""
        group = await self.get(group_id)
        members = await self._active_members(group)
        if any(name not in members for name in mention):
            raise ValueError("点名的成员不在这个群里")
        message = GroupMessage(message_id=f"group-message-{delivery_id or uuid4().hex}", sender=Speaker(kind="user", name=None),
                               reply_to=None, text=text, group=group.name, mentioned_agents=mention, origin=None)
        async with self._group_locks.setdefault(group_id, asyncio.Lock()):
            if (await self.get(group_id)).dissolved:
                raise GroupDissolvedError(group_id)
            previous = await self._load(group)
            await self._flush(previous)
            existing = next((item for item in await self.messages(group_id) if item.message_id == message.message_id), None)
            if existing is not None:
                if existing != message:
                    raise ValueError("group message delivery identity conflicts with its content")
                self._schedule(previous)
                return existing
            drive = self._replace_drive(group)
            drive.queue(None, [members[name] for name in mention], _Call(message.message_id, message.message_id, 1))
            drive.outbox[message.message_id] = message
            await self._save(drive, receipt=(message.message_id, True))
            await self._flush(drive)
            self._schedule(drive)
        return message

    def _replace_drive(self, group: GroupRecord) -> _Drive:
        previous = self._drives.get(group.group_id)
        if previous is not None:
            previous.replaced.set()
        drive = _Drive(group)
        self._drives[group.group_id] = drive
        return drive

    def _schedule(self, drive: _Drive) -> None:
        if (drive.pending or drive.batch) and not drive.replaced.is_set() and not drive.running:
            drive.running = True
            self.host._track(drive.group.group_id, asyncio.create_task(self._run(drive)))

    async def _run(self, drive: _Drive) -> None:
        try:
            while (drive.pending or drive.batch) and not drive.replaced.is_set():
                async with self._group_locks[drive.group.group_id]:
                    if drive.replaced.is_set() or self.host.closed:
                        return
                    if not drive.batch:
                        drive.batch, drive.pending = drive.pending, {}
                    name, call = next(iter(drive.batch.items()))
                    if drive.active is None:
                        drive.active = name
                        drive.answering[name, call.message_id] = call
                        await self._save(drive)
                await self._call(drive, name, call)
                async with self._group_locks[drive.group.group_id]:
                    if drive.replaced.is_set():
                        return
                    del drive.batch[name]
                    drive.active = None
                    await self._save(drive)
        finally:
            # Cleared before any await so a speech arriving now starts a new run.
            drive.running = False
            self._changed(drive.group.group_id)

    async def _call(self, drive: _Drive, name: str, call: _Call) -> None:
        """Deliver the response request without waiting for the member's work.

        A member may start tools immediately and report much later. Its response
        request must not hold up delivery to anyone else, including later work.
        Re-delivery after a restart uses the original fact identity.
        """
        agent = await self.host.agents.by_session(drive.group.member_sessions[name])
        if agent.deleted:
            return
        delivery_id = f"attention:{drive.group.group_id}:{call.message_id}:{name}"
        attention = fact_arguments(GroupAttention(group=drive.group.name, message_id=call.message_id),
                                   delivery_id=delivery_id)
        self._changed(drive.group.group_id)
        try:
            await self.host.request("fact", agent.session_id, attention)
        except AgentDeletedError:
            return

    async def send(self, session_id: str, arguments: dict, origin: str, admit):
        """An Agent speech. ``admit`` checks the per-origin budget once the send is known to be new.

        The speech answers a call only when its ``reply_to`` quotes the message the
        sender was called to answer and that call is still open; each call is
        answered once. Which message a speech answers is the model's statement and
        is never inferred from what started its work.
        """
        sender = await self.host.agents.by_session(session_id)
        group = next((group for group in await self.list()
                      if group.name == arguments["group"]), None)
        if group is None:
            return {"ok": False, "code": "GROUP_NOT_FOUND", "error": "没有这个群。"}
        members = await self._active_members(group)
        if sender.deleted or session_id not in group.member_sessions.values():
            return {"ok": False, "code": "NOT_A_MEMBER", "error": "你不在这个群里。"}
        mention = arguments["mention"]
        async with self._group_locks.setdefault(group.group_id, asyncio.Lock()):
            if (await self.get(group.group_id)).dissolved:
                return {"ok": False, "code": "GROUP_NOT_FOUND", "error": "这个群已解散，不能再向它发言。"}
            drive = await self._load(group)
            await self._flush(drive)
            messages = await self.messages(group.group_id)
            identity = arguments["message_id"]
            existing = next((message for message in messages if message.message_id == identity), None)
            if existing is not None:
                if (await self.host.agents.sender_session(existing) != session_id or existing.group != group.name
                        or existing.text != arguments["text"] or existing.mentioned_agents != mention
                        or existing.reply_to != arguments["reply_to"]):
                    raise ValueError("persisted group message conflicts with its sending Command")
                await self._publish(group, existing)
                self._schedule(drive)
                return self._delivered(group, identity, await self._receipt(group.group_id, identity))
            if any(name not in members or name == sender.name for name in mention):
                return {"ok": False, "code": "INVALID_MENTION", "error": "只能点名这个群里的其他成员。"}
            if arguments["reply_to"] is not None and all(
                    message.message_id != arguments["reply_to"] for message in messages):
                return {"ok": False, "code": "INVALID_REPLY", "error": "引用的消息必须是这个群里的消息。"}
            refused = await admit(group.name)
            if refused is not None:
                return refused
            speaker = members[sender.name]
            mentioned = [members[name] for name in mention]
            answered = drive.answering.get((speaker, arguments["reply_to"]))
            if answered is not None and drive.replies[answered.opener] >= MAX_GROUP_REPLIES:
                return {"ok": False, "code": "GROUP_LIMIT_REACHED",
                        "error": "这次群讨论的回复已达上限；需要某位同事接着处理时可以私信他。"}
            message = GroupMessage(message_id=identity, sender=Speaker(kind="agent", name=sender.name),
                                   sender_session_id=session_id,
                                   reply_to=arguments["reply_to"], text=arguments["text"], group=group.name,
                                   mentioned_agents=mention, origin=origin)
            if answered is not None:
                del drive.answering[speaker, arguments["reply_to"]]
            if answered is None:
                calls = drive.queue(speaker, mentioned, _Call(identity, identity, 1))
            else:
                drive.replies[answered.opener] += 1
                calls = drive.queue(speaker, mentioned, _Call(identity, answered.opener, answered.round + 1))
            drive.outbox[identity] = message
            await self._save(drive, receipt=(identity, calls))
            await self._flush(drive)
            self._schedule(drive)
            return self._delivered(group, identity, calls)

    def _delivered(self, group, identity, calls):
        notice = ("已送达。" if calls is None else "已送达；已请相关成员分别处理，不等待回复。" if calls else
                  "已送达；这次群讨论已达轮数上限，不会再叫成员回应；需要某位同事接着处理时可以私信他。")
        return {"ok": True, "code": "DELIVERED", "data": {"message_id": identity, "group": group.name,
                "group_path": f"/groups/{group.group_id}", "notice": notice}}
