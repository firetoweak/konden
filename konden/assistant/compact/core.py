"""Assistant-owned background handoff and context-window projection."""

from __future__ import annotations

from konden.runtime.json_values import thaw_value

import json
from copy import deepcopy
from dataclasses import replace
from datetime import datetime

from konden.assistant.agents import identity_for_model
from konden.assistant.artifacts import (
    is_valid_artifact_id,
    ArtifactNotFoundError,
)
from konden.assistant.attachments import is_valid_attachment_id
from konden.assistant.content import READ_CONTENT, read_content_result
from konden.assistant.context.memory import memory_observations, memory_message
from konden.assistant.context.projection import (
    _translate_visible_events,
    catalog_for_model,
    PreparedModelContext,
)
from konden.assistant.control import project_pending_approval
from konden.assistant.subagent.subagent import project_parent, project_returned
from konden.assistant.workspaces import SESSION_WORKSPACE_FACT, SESSION_DIRECTORY_FACT
from konden.llm.api import InvalidLLMResponse
from konden.runtime import DomainFactCommitted, StepCommitted, ToolBinding, RuntimeStatus
from konden.runtime.state import StateProjector

TASK = "compact.task"
CREATED = "compact.handoff_created"
WINDOW = "compact.window_rolled_over"
MODEL_USAGE = "model_usage"
FIND_HISTORY = "find_history"
SUBMIT = "_accept_handoff"
HISTORY_PREVIEW_CHARS = 400
MAX_HISTORY_RECORDS = 50
PURPOSE = """<self_handoff>
请为截至目前的对话整理交接，不继续用户业务，不向用户或同事发消息。
优先使用已有上下文，仅为关键缺口用 find_history 定位历史、read_content 读取真实引用。其他工具不能执行。
交接只保留仍影响后续工作的目标、约束、纠正、决定、进展、未完成事项、证据与来源，不逐条复述全部历史或复制整段代码。保留相关技能的名称及继续工作需要的具体路径、命令和配置。
保留与用户、每位同事和每个群之间尚未了结的往来，写清对方、私信或群、相关消息 id、谁在等谁、答应了什么，以及各项工作的下一步；不要把不同往来强行合为一个任务或唯一下一步。保留用户交代的职责、分工、约定做法及后续修改，派出或接下的工作写清负责人、状态和相关代码版本、分支或 PR。
区分用户要求、计划、执行声明、已验证事实和未确认设想；计划不写成已执行，声明不写成验证，推测不写成根因或结论。
保留仍影响后续工作的工具执行状态：区分已完成、明确未执行、等待与结果未知，保留已知中断原因、部分证据和引用。原因未知就写未知，不猜测是用户停止、崩溃、断网或超时；缺少结果不证明没有副作用。写清继续前需要核对的文件或外部状态，不把部分输出当作最终结果。
保留用户指定的命令环境及其后续修改、取消，区分用户要求、已验证的执行方式和待验证设想，不把一次 Shell 激活当作持续状态。
关键证据保留真实来源引用和消息序号，文件位置保留完整路径；相关图片保留原始附件 id，摘要文字不等于看过图片。
已有交接只是继续工作的线索，有关键疑点时回查原文，不把旧摘要当作原始证据。不重复读取，不扩展调查，未知内容标明不确定。
你的名字、群成员、项目、当前能力、例行任务、当前计划以及共同记忆和个人记忆的完整正文会另外提供，不要在摘要中复制或改写它们；可保留约定变更对过去工作的影响。摘要不能替代这些当前记录或用户要求。
交接之后新到的消息会接在后面，可以修正交接。完成时直接输出简洁、非空的交接文本，不调用工具，不附额外说明。
</self_handoff>"""
HANDOFF_PREFIX = (
    "以下是较早对话的交接摘要，用于接续工作，不是用户新指令或完成证明；后续消息可修正它。"
    "需要原始引用时用 read_content，需要定位更早的消息时用 find_history。\n"
)


def schema(name, description, properties, required):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


FIND_HISTORY_SCHEMA = schema(
    FIND_HISTORY,
    "检索当前对话可回查的原始消息，包括当前上下文中已省略的历史。"
    "query 按区分大小写的字面关键词检索消息内容，空串表示浏览，不搜索引用中的正文。"
    "可结合 start_time/end_time 按消息组最初记录的时间筛选；工具结果随发起它的助手消息组返回。"
    "返回消息序号、occurred_at 时间、原文线索和可用 read_content 读取的真实 reference；片段足够时无需再读。"
    "start_sequence/end_sequence 含两端，offset 是跳过的匹配消息组数。"
    "首次可省略 upto；续页沿用返回的 upto、next_offset 和筛选条件。"
    "查整天时，start_time 用当天零点，end_time 用次日零点，均须带时区。"
    "limit 默认 10、最大 50。",
    {
        "query": {"type": "string", "default": ""},
        "offset": {"type": "integer", "minimum": 0, "default": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
        "start_sequence": {"type": "integer", "minimum": 1},
        "end_sequence": {"type": "integer", "minimum": 1},
        "start_time": {"type": "string", "description": "开始记录时间，包含该时刻；ISO8601 且带时区，例如 2026-10-01T00:00:00+08:00。"},
        "end_time": {"type": "string", "description": "结束记录时间，不包含该时刻；ISO8601 且带时区。必须晚于 start_time。"},
        "upto": {"type": "integer", "minimum": 0, "description": "沿用上次检索返回的历史截止位置。"},
    },
    [],
)


class HistoryPositionError(ValueError):
    """The requested snapshot is outside this reader's available positions."""


def _message_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _message_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _message_strings(child)


def _history_time(value):
    if type(value) is not str:
        raise ValueError("历史时间必须是带时区的 ISO8601 字符串。")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("历史时间必须带时区，例如 2026-10-01T00:00:00+08:00。")
    return parsed


def _history_record(sequence, messages, query, occurred_at):
    texts = list(_message_strings(messages))
    if query:
        text = next((text for text in texts if query in text), None)
        if text is None:
            return None
        start = max(0, text.index(query) - 80)
    else:
        text = "\n".join(texts)
        start = 0
    end = min(len(text), start + HISTORY_PREVIEW_CHARS)
    return {
        "sequence": sequence,
        "occurred_at": occurred_at,
        "roles": list(dict.fromkeys(message["role"] for message in messages)),
        "preview": text[start:end],
        "preview_truncated": start > 0 or end < len(text),
    }


def compact_seed(events):
    seeds = [
        e.payload
        for e in events
        if isinstance(e.payload, DomainFactCommitted) and e.payload.fact_type == TASK
    ]
    if not seeds:
        return None
    if (
        len(seeds) != 1
        or len(events) < 2
        or not isinstance(events[0].payload, DomainFactCommitted)
        or events[0].payload.fact_type not in {SESSION_WORKSPACE_FACT, SESSION_DIRECTORY_FACT}
        or events[1].payload is not seeds[0]
    ):
        raise ValueError("compact task must follow the unique workspace binding")
    # 自产 seed：缺键由后续访问 KeyError 冒泡，不校验精确字段集合。
    return TASK, thaw_value(seeds[0].data)


def attachment_ids_in_messages(messages):
    ids = set()
    for message in messages:
        content = message.get("content")
        if type(content) is not list:
            continue
        for part in content:
            if type(part) is dict and is_valid_attachment_id(part.get("id")):
                ids.add(part["id"])
    return frozenset(ids)


def load_document(gateway, session, reference):
    store = gateway.for_session(session)
    first = store.read(reference, 0, 1)
    return json.loads(store.read(reference, 0, max(1, first.total_chars)).content)


def save_document(gateway, session, value):
    return (
        gateway.for_session(session)
        .save(json.dumps(value, ensure_ascii=False))
        .artifact_id
    )


def window_fact(events):
    result = None
    for event in events:
        if (
            isinstance(event.payload, DomainFactCommitted)
            and event.payload.fact_type == WINDOW
        ):
            data = thaw_value(event.payload.data)
            if data["parent"] != (None if result is None else result["id"]):
                raise ValueError("broken context window lineage")
            result = data
    return result


def latest_input_tokens(events):
    """Read the last committed model usage belonging to the active window."""
    current = window_fact(events)
    window = None if current is None else current["id"]
    for event in reversed(events):
        payload = event.payload
        if isinstance(payload, DomainFactCommitted) and payload.fact_type == WINDOW:
            return None
        if isinstance(payload, StepCommitted):
            metadata = payload.decision_metadata
            if metadata is None:
                continue
            usage = metadata[MODEL_USAGE]
            return usage["input_tokens"] if usage["window"] == window else None
    return None


class CompactContext:
    def __init__(self, session_id, events, projector, transport):
        self.session_id = session_id
        self.projector = projector
        self.transport = transport
        self.runtime = None
        self.seed = compact_seed(events)
        self.prefix = []
        self.window = None
        self.request = None
        self._history = None
        self._history_query = None
        if self.is_reader:
            data = self.seed[1]
            self.request = load_document(
                projector.gateway, data["source"], data["inherited"]
            )
        self.refresh(events)

    @property
    def is_reader(self):
        return self.seed is not None

    def refresh(self, events):
        self.window = window_fact(events)
        self.prefix = []
        if self.window is not None:
            self.prefix = load_document(
                self.projector.gateway, self.session_id, self.window["context"]
            )["messages"]

    def read_attachment(self, attachment_id):
        source = (
            self.seed[1]["source"]
            if (
                self.is_reader
                and attachment_id
                in attachment_ids_in_messages(self.request["messages"])
            )
            else self.session_id
        )
        return self.projector.attachments_for(source).read(attachment_id)

    def schemas(self):
        return deepcopy(self.request["tools"]) if self.is_reader else [FIND_HISTORY_SCHEMA]

    def visible(self, events, state):
        # 按 sequence 硬切不会把一个回合切成两半：cutover 取自 snapshot 时的
        # journal_position，而 snapshot 在还有命令未终局时直接拒绝，所以截断点
        # 之前的每个 Step 连同它的全部命令事件都已落盘。
        self.refresh(events)
        cutoff = 0 if self.window is None else self.window["cutover"]
        allowed = {
            e.event_id
            for e in events
            if e.sequence > cutoff
            and not (
                isinstance(e.payload, DomainFactCommitted)
                and e.payload.fact_type in (WINDOW, CREATED)
            )
        }
        return StateProjector().project_visible(
            state.session_id,
            events,
            tuple(x for x in state.visible_event_ids if x in allowed),
        )

    def bindings(self):
        return {
            FIND_HISTORY: ToolBinding(self.find_history),
            READ_CONTENT: ToolBinding(self.read_content),
            SUBMIT: ToolBinding(self.submit, decision_on_outcome=False),
        }

    async def _read_source(self, upto):
        if self.is_reader:
            data = self.seed[1]
            if upto is not None and upto != data["upto"]:
                raise HistoryPositionError(f"当前可读历史固定截至 {data['upto']}，请用 end_sequence 筛选更早范围")
            if self._history is None:
                bundle = load_document(
                    self.projector.gateway, data["source"], data["bundle"]
                )
                bundle["artifacts"] = sorted(
                    set(bundle["artifacts"]) | {data["inherited"], data["bundle"]}
                )
                self._history = data["source"], bundle, data["upto"]
            return self._history
        events = await self.runtime.snapshot(self.session_id)
        available = events[-1].sequence if events else 0
        upto = available if upto is None else upto
        if upto > available:
            raise HistoryPositionError(f"当前可回查的历史截至消息序号 {available}，请提供不超过该值的 upto")
        if self._history is not None and self._history[2] == upto:
            return self._history
        # 截取执行事实后再投影，不能让截止位置之后的 Outcome 改写旧 Step 的表示。
        selected = tuple(event for event in events if event.sequence <= upto)
        self._history = self.session_id, history_bundle(self.projector, selected, self.session_id), upto
        return self._history

    async def prepare_reader(self, events, state):
        own = _translate_visible_events(
            events,
            state,
            "",
            self.projector.attachments_for(self.session_id),
        )[1:]
        messages = deepcopy(self.request["messages"])
        messages.append({
            "role": "user",
            "content": PURPOSE + f"\n当前可检索的历史截至消息序号 {self.seed[1]['upto']}。",
        })
        messages.extend(item.message for item in own)
        return PreparedModelContext(
            messages=messages,
            protection_start_index=0,
            size_externalized_command_ids=(),
            age_dehydrated_command_ids=(),
            source_sequences=tuple([0] * len(messages)),
        )

    async def find_history(self, context, arguments):
        offset, limit = arguments.get("offset", 0), arguments.get("limit", 10)
        upto = arguments.get("upto")
        start_sequence = arguments.get("start_sequence", 1)
        end_sequence = arguments.get("end_sequence")
        query = arguments.get("query", "")
        if (
            set(arguments) - {"offset", "limit", "upto", "start_sequence", "end_sequence", "query", "start_time", "end_time"}
            or type(offset) is not int or offset < 0
            or type(limit) is not int or not 1 <= limit <= MAX_HISTORY_RECORDS
            or ("upto" in arguments and (type(upto) is not int or upto < 0))
            or type(start_sequence) is not int or start_sequence < 1
            or ("end_sequence" in arguments and (type(end_sequence) is not int or end_sequence < start_sequence))
            or type(query) is not str
        ):
            return {"ok": False, "code": "INVALID_ARGUMENT", "error": (
                "参数无效：只接受 query、offset、limit、upto、start_sequence、end_sequence、start_time、end_time；"
                "offset ≥ 0，limit 为 1–50，upto ≥ 0，start_sequence ≥ 1，end_sequence ≥ start_sequence。"
            )}
        try:
            start_time = _history_time(arguments["start_time"]) if "start_time" in arguments else None
            end_time = _history_time(arguments["end_time"]) if "end_time" in arguments else None
        except ValueError:
            return {"ok": False, "code": "INVALID_ARGUMENT", "error": "start_time/end_time 必须是带时区的 ISO8601 时间，例如 2026-10-01T00:00:00+08:00。"}
        if start_time is not None and end_time is not None and start_time >= end_time:
            return {"ok": False, "code": "INVALID_ARGUMENT", "error": "end_time 必须晚于 start_time；开始时间包含，结束时间不包含。"}
        if offset > 0 and upto is None:
            return {"ok": False, "code": "INVALID_ARGUMENT", "error": "续查必须提供第一页返回的 upto"}
        try:
            source, bundle, upto = await self._read_source(upto)
        except HistoryPositionError as error:
            return {"ok": False, "code": "HISTORY_POSITION_OUT_OF_RANGE", "error": str(error)}
        end_sequence = upto if end_sequence is None else end_sequence
        if end_sequence > upto:
            return {"ok": False, "code": "INVALID_ARGUMENT", "error": "end_sequence 不能超过 upto"}
        key = upto, start_sequence, end_sequence, query, start_time, end_time
        if self._history_query is None or self._history_query[0] != key:
            records = []
            for sequence in sorted(bundle["raw"], key=int):
                if start_sequence <= int(sequence) <= end_sequence:
                    occurred_at = bundle["timestamps"][sequence]
                    if start_time is not None or end_time is not None:
                        moment = datetime.fromisoformat(occurred_at)
                        if start_time is not None and moment < start_time:
                            continue
                        if end_time is not None and moment >= end_time:
                            continue
                    record = _history_record(int(sequence), bundle["raw"][sequence], query, occurred_at)
                    if record is not None:
                        records.append(record)
            self._history_query = key, records
        records = self._history_query[1]
        if offset > len(records):
            return {"ok": False, "code": "OFFSET_OUT_OF_RANGE", "error": f"offset 超过匹配总数 {len(records)}"}
        end = min(len(records), offset + limit)
        page = [dict(record) for record in records[offset:end]]
        store = self.projector.gateway.for_session(self.session_id)
        for record in page:
            record["reference"] = store.save(json.dumps(
                bundle["raw"][str(record["sequence"])], ensure_ascii=False,
            )).artifact_id
        return {
            "ok": True, "code": "HISTORY_FOUND",
            "data": {
                "upto": upto, "start_sequence": start_sequence, "end_sequence": end_sequence,
                "start_time": arguments.get("start_time"), "end_time": arguments.get("end_time"),
                "records": page, "offset": offset,
                "next_offset": end if end < len(records) else None,
                "total_records": len(records),
            },
            "hint": "线索不足时用 read_content 读取 reference；继续检索沿用 upto、next_offset 和筛选条件。",
        }

    async def read_content(self, context, arguments):
        source = self.session_id
        if self.is_reader:
            reference = arguments.get("reference")
            if not is_valid_artifact_id(reference):
                return read_content_result(self.projector.gateway.for_session(source), arguments)
            try:
                self.projector.gateway.for_session(source).read(reference, 0, 1)
            except ArtifactNotFoundError:
                pass
            else:
                return read_content_result(self.projector.gateway.for_session(source), arguments)
            source, bundle, _ = await self._read_source(None)
            if reference not in bundle["artifacts"]:
                return {"ok": False, "code": "CONTENT_NOT_FOUND", "error": "该引用不在当前可读内容中。"}
        return read_content_result(self.projector.gateway.for_session(source), arguments)

    async def submit(self, context, arguments):
        if not self.is_reader or set(arguments) != {"handoff"}:
            raise ValueError("invalid internal handoff submission")
        text = arguments["handoff"]
        if type(text) is not str or not text.strip():
            raise InvalidLLMResponse("invalid_handoff", "handoff must be nonempty")
        await self.transport("compact_complete", self.session_id, {"handoff": text})
        return {"ok": True, "code": "HANDOFF_SUBMITTED", "data": {"submitted": True}}


def frozen_bundle(projector, events, session_id, context, prepared=None):
    whole = StateProjector().project_visible(session_id, events)
    visible = context.visible(events, whole)
    if prepared is None:
        prepared = projector.prepare(
            events, visible, session_id, "", prefix=context.prefix
        )
    records = [
        {"source": session_id, "sequence": seq, "message": message}
        for seq, message in zip(prepared.source_sequences[1:], prepared.messages[1:])
    ]
    bundle = history_bundle(projector, events, session_id)
    artifacts = set(bundle["artifacts"])
    for record in records:
        message = record["message"]
        if message["role"] == "tool":
            artifacts.update(content_references(json.loads(message["content"])))
    return {"records": records, **bundle, "artifacts": sorted(artifacts),
            "memories": memory_observations(events)}


def content_references(value):
    references = set()
    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"artifact_id", "reference"} and is_valid_artifact_id(child):
                    references.add(child)
                else:
                    collect(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child)
    collect(value)
    return references


def history_bundle(projector, events, session_id):
    """Original model messages and referenced artifacts at one Journal position."""
    whole = StateProjector().project_visible(session_id, events)
    raw = {}
    for item in _translate_visible_events(
        events, whole, "", projector.attachments_for(session_id)
    ):
        if item.sequence:
            raw.setdefault(str(item.sequence), []).append(item.message)
    artifacts = set()


    for values in raw.values():
        for message in values:
            if message["role"] == "tool":
                artifacts.update(content_references(json.loads(message["content"])))
    for event in events:
        payload = event.payload
        if isinstance(payload, DomainFactCommitted):
            if payload.fact_type == CREATED:
                artifacts.update((payload.data["artifact"], payload.data["request"]))
            elif payload.fact_type == WINDOW:
                artifacts.update((payload.data["context"], payload.data["bundle"]))
    timestamps = {
        str(event.sequence): event.occurred_at.isoformat()
        for event in events if str(event.sequence) in raw
    }
    return {"raw": raw, "timestamps": timestamps, "artifacts": sorted(artifacts)}


def window_preamble(summary, upto, catalog, identity, memories):
    """A new window restates what earlier facts established before the handoff text."""
    before = []
    if identity is not None:
        before.append({"role": "user", "content": json.dumps(
            {"fact": "bot.identity", "data": identity}, ensure_ascii=False)})
    before.append({
        "role": "user",
        "content": HANDOFF_PREFIX + f"这份交接覆盖的历史截至消息序号 {upto}。\n" + summary,
    })
    if catalog is not None:
        before.append({
            "role": "user",
            "content": "<capability_catalog>\n"
            + json.dumps({"fact": "assistant.catalog", "data": catalog_for_model(catalog)},
                         ensure_ascii=False)
            + "\n</capability_catalog>",
        })
    before.extend(memory_message(kind, data) for kind, data in memories.items())
    return before


def projected_tail(records, p, q):
    return [r["message"] for r in records if p < r["sequence"] <= q]


class CompactBoundary:
    def __init__(self, runtime, decision, context, config, control, transport, *, model_selection_source=None):
        self.runtime, self.decision, self.context = runtime, decision, context
        self.config, self.control, self.transport = config, control, transport
        self.scheduler = None
        self.model_selection_source = model_selection_source

    async def snapshot(self):
        sid = self.context.session_id
        events = await self.runtime.snapshot(sid)
        state = self.runtime.projector.project(sid, events).state
        if (
            state.waiting_command_ids
            or project_pending_approval(events) is not None
        ):
            return {"safe": False}
        visible = self.context.visible(
            events, StateProjector().project_visible(sid, events)
        )
        prompt = self.decision.prompt_for(state)
        tools = self.decision.schemas_for(state, events)[0]
        prepared = self.context.projector.prepare(
            events,
            visible,
            sid,
            prompt,
            prefix=self.context.prefix,
        )
        bundle = frozen_bundle(
            self.context.projector, events, sid, self.context, prepared
        )
        catalog = next(
            (
                thaw_value(e.payload.data)
                for e in reversed(events)
                if isinstance(e.payload, DomainFactCommitted)
                and e.payload.fact_type == "assistant.catalog"
            ),
            None,
        )
        return {
            "safe": True,
            "position": state.journal_position,
            "window": None
            if self.context.window is None
            else self.context.window["id"],
            "bundle": save_document(self.context.projector.gateway, sid, bundle),
            "inherited": save_document(
                self.context.projector.gateway,
                sid,
                {
                    "model": self.config.model_name,
                    "reasoning_effort": self.config.reasoning_effort,
                    "messages": prepared.messages,
                    "tools": tools,
                },
            ),
            "catalog": catalog,
            "identity": await self._identity(events),
        }

    async def _identity(self, events):
        identity = identity_for_model(events)
        if identity is not None and self.transport is not None:
            listed = await self.transport("list_routines", self.context.session_id, {})
            identity["routines"] = listed["data"]["routines"]
            identity["notice"] += "routines 列出了你当前的常设例行任务。"
        return identity

    async def publish(self, arguments):
        sid = self.context.session_id
        events = await self.runtime.snapshot(sid)
        current = window_fact(events)
        data = arguments["window"]
        if current is not None and current["id"] == data["id"]:
            if current != data:
                raise ValueError("conflicting context window publication")
            return True
        if project_parent(events) is not None and project_returned(events):
            return False
        if data["parent"] != (None if current is None else current["id"]):
            raise ValueError("stale handoff window")
        await self.runtime.receive_domain_fact(
            sid,
            CREATED,
            arguments["handoff"],
            source="compact",
            delivery_id=data["id"] + ":handoff",
        )
        await self.runtime.receive_domain_fact(
            sid, WINDOW, data, source="compact", delivery_id=data["id"] + ":window"
        )
        self.context.refresh(await self.runtime.snapshot(sid))
        return True

    async def before_advance(self):
        sid = self.context.session_id
        if self.context.is_reader:
            return True
        events = await self.runtime.snapshot(sid)
        parent = project_parent(events)
        if parent is not None and project_returned(events):
            return False
        state = self.runtime.projector.project(sid, events).state
        if state.waiting_command_ids or project_pending_approval(events) is not None:
            return True
        if self.model_selection_source is not None:
            profile = await self.model_selection_source(state.status is RuntimeStatus.RUNNABLE)
            if profile is not None:
                self.config = replace(self.config, model_name=profile["model"],
                                      compact_threshold_tokens=profile["compact_threshold_tokens"],
                                      reasoning_effort=profile.get("reasoning_effort"))
                self.decision.set_model(profile["model"], profile["compact_threshold_tokens"],
                                        profile.get("reasoning_effort"))
        used = latest_input_tokens(events)
        response = await self.transport(
            "compact_boundary",
            sid,
            {"pressure": used is not None and used >= self.config.compact_threshold_tokens},
        )
        if response == "wait":
            return False
        if response != "continue":
            raise ValueError("invalid compact boundary response")
        return True
