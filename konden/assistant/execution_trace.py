"""Read-only execution evidence for the Bot's Journal and its delegated sessions."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import json

from konden.assistant.agents import project_agent
from konden.assistant.artifacts import FileArtifactGateway, is_valid_artifact_id
from konden.assistant.attachments import is_image_attachment_id
from konden.assistant.catalog import CATALOG
from konden.assistant.compact.core import CREATED, MODEL_USAGE, WINDOW, compact_seed
from konden.assistant.context.projection import MESSAGE_EXTENSIONS
from konden.assistant.host.session_store import SessionStore
from konden.assistant.runner import SessionNotFoundError
from konden.assistant.subagent.subagent import project_delegate_intents, project_task
from konden.runtime.events import (
    DomainFactCommitted,
    StepCommitted,
    UserMessageReceived,
)
from konden.runtime.json_values import thaw_value
from konden.runtime.model import CommandState, OutcomeStatus

_ARGUMENT_KEYS = ("path", "file_path", "command", "query", "pattern", "url", "recipient", "skill_id", "toolset_id", "task")
_USAGE_KEYS = ("input_tokens", "output_tokens", "cached_input_tokens")


def _preview(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = " ".join(value[:181].split())
    return text[:180] + ("…" if len(value) > 180 else "")


def _argument_preview(command) -> str:
    arguments = dict(command.effect.arguments)
    for key in _ARGUMENT_KEYS:
        preview = _preview(arguments.get(key))
        if preview:
            text = _preview(arguments.get("text")) if key == "recipient" else ""
            return _preview(f"{key} = {preview}" + (f" · {text}" if text else ""))
    return ""


def _result_preview(outcome) -> str:
    if outcome is None:
        return ""
    value = outcome.value
    data = value.get("data") if isinstance(value, Mapping) else None
    candidates = [outcome.error_message, value]
    if isinstance(value, Mapping):
        candidates.append(value.get("error"))
        if isinstance(data, Mapping):
            for stream in ("stderr", "stdout"):
                chunks = data.get(stream)
                if isinstance(chunks, Mapping):
                    candidates.append(chunks.get("content"))
            candidates.extend(data.get(key) for key in ("content", "preview", "path"))
        candidates.append(value.get("code"))
    return next((text for candidate in candidates if (text := _preview(candidate))), "")


def _at(event) -> float:
    return event.occurred_at.timestamp()


def _entry(kind, event, title, *, start=None, **fields):
    return {"id": f"{kind}:{event.sequence}", "kind": kind, "sequence": event.sequence,
            "at": _at(event), "start": _at(event) if start is None else start,
            "title": title, **fields}


def _usage(payload):
    metadata = payload.decision_metadata
    usage = metadata.get(MODEL_USAGE) if isinstance(metadata, Mapping) else None
    if not isinstance(usage, Mapping):
        return {}
    return {key: usage[key] for key in _USAGE_KEYS if type(usage.get(key)) is int and usage[key] >= 0}


def _command_events(command: CommandState, by_id):
    issued = by_id[command.issued_by_event_id]
    terminal = by_id.get(command.canonical_outcome_event_id)
    attempt = command.attempts[-1] if command.attempts else None
    if terminal is not None:
        attempt = next((item for item in command.attempts if item.attempt_id == terminal.payload.attempt_id), None)
    dispatch = by_id[attempt.started_event_id] if attempt is not None else None
    return issued, dispatch, terminal


def _status(command):
    outcome = command.outcome
    if outcome is not None:
        if outcome.status is OutcomeStatus.SUCCEEDED:
            return "failed" if isinstance(outcome.value, Mapping) and outcome.value.get("ok") is False else "passed"
        return outcome.status.value
    if command.authorization_rejected_by_event_id is not None:
        return "rejected"
    return "unknown" if command.attempts else "pending"


def _compact_entries(events):
    handoffs = {}
    windows = {}
    for event in events:
        payload = event.payload
        if isinstance(payload, DomainFactCommitted):
            if payload.fact_type == CREATED:
                handoffs[payload.data["reader"]] = event
            elif payload.fact_type == WINDOW:
                windows[payload.data["id"]] = event
    for reader in dict.fromkeys((*handoffs, *windows)):
        handoff = handoffs.get(reader)
        yield windows.get(reader, handoff), handoff, reader in windows


def _index(events, state, compacts):
    by_id = {event.event_id: event for event in events}
    commands = {command.command.command_id: command for command in state.commands}
    delegates = {intent.command_id: intent for intent in project_delegate_intents(events)}
    entries = []
    ordinal = 0
    first_catalog = True
    consumed_user_ids = set()
    for event in events:
        payload = event.payload
        if isinstance(payload, StepCommitted):
            ordinal += 1
            step = payload.step
            triggers = [by_id[event_id] for event_id in step.trigger_event_ids]
            users = [trigger for trigger in triggers if isinstance(trigger.payload, UserMessageReceived)]
            consumed_user_ids.update(user.event_id for user in users)
            title = f"第 {ordinal} 步"
            entries.append(_entry("input", event, title, start=max(map(_at, triggers)), user=bool(users), step=ordinal))
            entries.append(_entry("model", event, title, step=ordinal, usage=_usage(payload),
                                  preview=_preview(step.decision.content) or ("仅工具调用" if step.commands else "无文本内容")))
            for index, request in enumerate(step.commands):
                command = commands[request.command_id]
                _, dispatch, terminal = _command_events(command, by_id)
                anchor = dispatch or event
                entry = _entry("tool", event, request.effect.name, start=_at(anchor), step=ordinal,
                               end=_at(terminal) if terminal and dispatch else None, status=_status(command),
                               preview=_argument_preview(request), result=_result_preview(command.outcome))
                entry.update(id=f"tool:{event.sequence}:{index}", at=_at(anchor))
                entries.append(entry)
                intent = delegates.get(request.command_id)
                if intent is not None:
                    entries.append(_entry("subagent", event, "子 Agent", start=_at(anchor), step=ordinal,
                                          preview=_preview(intent.task), branch=intent.command_id) |
                                   {"id": f"subagent:{event.sequence}:{index}"})
        elif isinstance(payload, DomainFactCommitted) and payload.fact_type == CATALOG:
            title = "工具注册" if first_catalog else "工具目录更新"
            entries.append(_entry("catalog", event, title, preview=f"{len(payload.data['toolsets'])} 个工具集"))
            first_catalog = False
    entries.extend(_entry("user", event, "用户消息", preview=_preview(event.payload.content),
                          user=True, on_axis=event.event_id not in consumed_user_ids)
                   for event in events if isinstance(event.payload, UserMessageReceived))
    entries.extend(compacts)
    return sorted(entries, key=lambda entry: (entry["at"], entry["sequence"]))


class ExecutionTraceQueries:
    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self.artifacts = FileArtifactGateway(store.root)

    async def _scope(self, session_id, branch):
        events = await self.store.journal(session_id).snapshot(session_id)
        agent = project_agent(events)
        if agent is None:
            raise SessionNotFoundError(session_id)
        task = None
        for command_id in branch:
            intent = next((item for item in project_delegate_intents(events) if item.command_id == command_id), None)
            if intent is None:
                raise SessionNotFoundError(f"委派不存在：{command_id}")
            session_id, task = intent.child_session_id, intent.task
            if not self.store.path(session_id).parent.exists():
                events = ()
                continue
            events = await self.store.journal(session_id).snapshot(session_id)
            registered = project_task(events)
            if (registered is None or registered.parent_session_id != intent.parent_session_id
                    or registered.delegate_command_id != command_id):
                raise ValueError("Subagent Journal does not match the committed delegation")
        return session_id, events, task

    async def session(self, session_id, branch):
        scoped_session_id, _, _ = await self._scope(session_id, branch)
        return scoped_session_id

    async def _compact_start(self, handoff):
        data = handoff.payload.data
        reader = data["reader"]
        if not self.store.path(reader).parent.exists():
            return None
        events = await self.store.journal(reader).snapshot(reader)
        seed = compact_seed(events)
        if seed is None or (seed[1]["source"], seed[1]["window"], seed[1]["upto"]) != (
            handoff.session_id, data["source_window"], data["upto"],
        ):
            raise ValueError("Compact task does not match its handoff")
        return _at(events[1])

    async def index(self, session_id, branch):
        session_id, events, task = await self._scope(session_id, branch)
        if not events:
            return {"session_id": session_id, "revision": 0, "task": task, "entries": []}
        events, state = await self.store.projected(session_id)
        compacts = []
        for event, handoff, published in _compact_entries(events):
            started = await self._compact_start(handoff) if handoff is not None else None
            compacts.append(_entry("compact", event, "Compact", start=started, end=_at(event),
                                   preview="已切换窗口" if published else "摘要已归档"))
        return {"session_id": session_id, "revision": state.journal_position, "task": task,
                "entries": await asyncio.to_thread(_index, events, state, compacts)}

    def _artifact(self, session_id, ref):
        store = self.artifacts.existing_session(session_id)
        first = store.read(ref, 0, 1)
        return first.content if first.next_offset is None else store.read(ref, 0, first.total_chars).content

    def _replay_manifest(self, event):
        for ref in event.artifact_refs:
            if not is_valid_artifact_id(ref):
                continue
            raw = self._artifact(event.session_id, ref)
            if raw.startswith("{"):
                manifest = json.loads(raw)
                if manifest.get("schema") == "decision-replay-manifest/v2":
                    return manifest
        return None

    def _step_detail(self, event, kind, events, state, part):
        step = event.payload.step
        if kind == "input":
            manifest = self._replay_manifest(event)
            if manifest is not None:
                return {"kind": kind, "request": manifest["request"], "triggers": []}
            by_id = {item.event_id: item for item in events}
            return {"kind": kind, "request": None, "triggers": [
                {"sequence": item.sequence, "kind": item.kind,
                 "text": item.payload.content if isinstance(item.payload, UserMessageReceived) else None}
                for event_id in step.trigger_event_ids if (item := by_id[event_id])
            ]}
        if kind == "model":
            metadata = event.payload.decision_metadata
            extensions = metadata.get(MESSAGE_EXTENSIONS) if isinstance(metadata, Mapping) else None
            reasoning = extensions.get("reasoning_content") if isinstance(extensions, Mapping) else None
            manifest = self._replay_manifest(event)
            if manifest is None:
                output = {
                    "content": step.decision.content,
                    "calls": [
                        {"name": effect.name, "arguments": effect.argument_dict()}
                        for effect in step.decision.command_requests
                    ],
                }
                source = "committed"
            else:
                response = manifest["response"]
                output = {"content": response["content"], "calls": response["calls"]}
                source = "recorded"
            return {"kind": kind, "output": output, "output_source": source,
                    "reasoning": reasoning if isinstance(reasoning, str) else None}
        if part is None or not 0 <= part < len(step.commands):
            raise SessionNotFoundError("调用不存在")
        command = state.command(step.commands[part].command_id)
        by_id = {item.event_id: item for item in events}
        issued, dispatch, terminal = _command_events(command, by_id)
        outcome = terminal.payload.outcome if terminal is not None else None
        return {"kind": kind, "arguments": command.command.effect.argument_dict(),
                "outcome": None if outcome is None else {
                    "status": outcome.status.value, "value": thaw_value(outcome.value),
                    "error_type": outcome.error_type, "error_message": outcome.error_message,
                }, "issued_at": _at(issued), "dispatched_at": _at(dispatch) if dispatch else None,
                "received_at": _at(terminal) if terminal else None,
                "attempts": [{"number": attempt.attempt_number, "at": _at(by_id[attempt.started_event_id]),
                              "phase": attempt.phase.value} for attempt in command.attempts]}

    async def detail(self, session_id, branch, entry):
        session_id, events, _ = await self._scope(session_id, branch)
        kind, _, address = entry.partition(":")
        sequence, separator, index = address.partition(":")
        if ((kind == "tool") != bool(separator) or not sequence.isascii() or not sequence.isdecimal()
                or len(sequence) > 20):
            raise SessionNotFoundError("轨迹节点不存在")
        position = int(sequence)
        if not 0 < position <= len(events):
            raise SessionNotFoundError("轨迹节点不存在")
        event = events[position - 1]
        payload = event.payload
        if isinstance(payload, StepCommitted) and kind in ("input", "model", "tool"):
            part = int(index) if index.isascii() and index.isdecimal() and len(index) <= 10 else None
            if kind == "tool":
                events, state = await self.store.projected(session_id)
            else:
                state = None
            return await asyncio.to_thread(self._step_detail, event, kind, events, state, part)
        if isinstance(payload, UserMessageReceived) and kind == "user":
            return {"kind": kind, "text": payload.content,
                    "images": [ref for ref in event.artifact_refs if is_image_attachment_id(ref)]}
        if isinstance(payload, DomainFactCommitted) and kind == "catalog" and payload.fact_type == CATALOG:
            first_catalog = next(item for item in events if isinstance(item.payload, DomainFactCommitted)
                                 and item.payload.fact_type == CATALOG)
            first_step = next((item for item in events if isinstance(item.payload, StepCommitted)), None)
            manifest = await asyncio.to_thread(self._replay_manifest, first_step) if first_catalog is event and first_step else None
            tools = manifest["request"]["tools"] if manifest is not None else None
            return {"kind": kind, "toolsets": thaw_value(payload.data["toolsets"]), "tools": tools or []}
        if isinstance(payload, DomainFactCommitted) and kind == "compact" and payload.fact_type in (CREATED, WINDOW):
            for anchor, handoff, published in _compact_entries(events):
                if anchor is event or handoff is event:
                    started_at = await self._compact_start(handoff) if handoff else None
                    summary = await asyncio.to_thread(self._artifact, session_id, handoff.payload.data["artifact"]) if handoff else None
                    return {"kind": kind, "published": published, "upto": anchor.payload.data["upto"],
                            "started_at": started_at, "at": _at(anchor),
                            "summary": json.loads(summary)["text"] if summary is not None else None}
        raise SessionNotFoundError("轨迹节点不存在")
