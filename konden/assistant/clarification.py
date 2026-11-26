"""A clarification is an assistant reply with optional choices, answered once in its session."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from konden.assistant.rest import REST
from konden.automation.once import MAX_DELAY_SECONDS
from konden.runtime import (
    CommandOutcome,
    CommandOutcomeReceived,
    CommandPhase,
    Event,
    EventDraft,
    InvokeTool,
    ModelDecision,
    OutcomeStatus,
    StepCommitted,
    UserMessageReceived,
    replay,
)
from konden.runtime.dispatcher import ToolBinding
from konden.runtime.model import argument_rejection, rejected_tool_arguments

ASK_USER = "ask_user"
CLARIFICATION_OPTIONS = "clarification_options"
ANSWER_SOURCE = "clarification_answer"
DEADLINE_REACHED = "clarification.deadline_reached"
SCHEDULE_PREFIX = "clarification:"
Nonblank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Deadline(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    after_seconds: int = Field(ge=1, le=MAX_DELAY_SECONDS, description="从发出问题起最多等多少秒。")
    plan: Nonblank = Field(description="届时仍缺少答复时打算怎么做。这段话会显示在卡片上。")


class Question(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    question: Nonblank = Field(description="用户可以直接回答的一个具体问题，带上必要背景。")
    options: list[Nonblank] = Field(
        default_factory=list, description="可选择的答复。需要说明时，首行写短标题，换行写完整说明。开放式问题可省略。",
    )

    @field_validator("options")
    @classmethod
    def distinct(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("选项不能重复")
        return values


class TimedQuestion(Question):
    deadline: Deadline | None = Field(default=None, description="能按明确假设推进时可设时限。用户之后仍可补答；必须等人决定时省略。")


def ask_user_schema(model):
    return {"type": "function", "function": {
        "name": ASK_USER, "parameters": model.model_json_schema(),
        "description": "在私聊中发一张可以点选或自由回答的澄清卡片。除 rest 外不和其他工具同批调用。发出后可继续做不依赖答案的工作；没有其他能做的事时，和 rest 同批调用。",
    }}


def clarification_reply(decision: ModelDecision, model: type[Question]) -> tuple[ModelDecision, list[str] | None]:
    asks = [call for call in decision.command_requests if call.name == ASK_USER]
    resting = tuple(call for call in decision.command_requests if call.name == REST)
    if len(asks) != 1 or len(asks) + len(resting) != len(decision.command_requests) \
            or argument_rejection(asks[0]) is not None:
        return decision, None
    arguments = asks[0].argument_dict()
    try:
        question = model.model_validate(arguments)
    except ValidationError as exc:
        errors = [f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors(include_url=False)]
        rejected = rejected_tool_arguments(
            json.dumps(arguments, ensure_ascii=False), code="VALIDATION_ERROR",
            error="；".join(errors), hint="修正问题和选项后重新提问。",
        )
        return ModelDecision(decision.content, (InvokeTool(ASK_USER, rejected), *resting)), None
    call = InvokeTool(ASK_USER, tuple(question.model_dump(exclude_none=True).items()))
    return ModelDecision(question.question, (call, *resting)), question.options


def questions_by_id(events: tuple[Event, ...]) -> dict[str, StepCommitted]:
    questions = {}
    for event in events:
        payload = event.payload
        if isinstance(payload, StepCommitted) and clarification_options(payload.decision_metadata) is not None:
            questions[payload.step.step_id] = payload
    return questions


def clarification_options(metadata: object) -> tuple[str, ...] | None:
    if isinstance(metadata, Mapping) and CLARIFICATION_OPTIONS in metadata:
        return tuple(metadata[CLARIFICATION_OPTIONS])
    return None


def question_text(content: str, options: Sequence[str] | None) -> str:
    if not options:
        return content
    return content + "\n" + "\n".join(f"- {option}" for option in options)


def deadline_for(step):
    for command in step.commands:
        if command.effect.name == ASK_USER:
            return command.effect.argument_dict().get("deadline")
    return None


def has_answer(events, question_id: str) -> bool:
    return any(isinstance(e.payload, UserMessageReceived) and e.delivery is not None
               and e.delivery.source == ANSWER_SOURCE and e.delivery.delivery_id == question_id for e in events)


async def _acknowledge(event: Event | None, transport):
    deadline = deadline_for(event.payload.step) if event is not None else None
    result = {"message": "问题已发出。继续处理不依赖答案的工作。只剩待答事项时就休息，答复到达会叫醒你。"}
    if deadline is not None:
        registered = await transport("schedule_once", event.session_id, {
            "schedule_id": SCHEDULE_PREFIX + event.payload.step.step_id, "delay_seconds": deadline["after_seconds"],
            "started_at": event.occurred_at.isoformat(), "purpose": deadline["plan"],
        })
        result.update(wait_until=registered["due_at"], plan=deadline["plan"])
    return {"ok": True, "code": "QUESTION_SENT", "data": result}


def clarification_binding(journal, transport):
    async def handler(context, arguments):
        if "deadline" not in arguments:
            return await _acknowledge(None, transport)
        events = await journal.snapshot(context.session_id)
        event = next(e for e in reversed(events) if isinstance(e.payload, StepCommitted)
                     and any(c.command_id == context.command_id for c in e.payload.step.commands))
        return await _acknowledge(event, transport)
    return ToolBinding(handler)


async def recover_clarification_attempts(journal, session_id, transport):
    events = await journal.snapshot(session_id)
    questions = {c.command_id: e for e in events if isinstance(e.payload, StepCommitted)
                 and clarification_options(e.payload.decision_metadata) is not None
                 for c in e.payload.step.commands if c.effect.name == ASK_USER}
    if not questions:
        return
    for item in replay(session_id, events).state.commands:
        if item.phase is not CommandPhase.UNKNOWN or item.command.command_id not in questions:
            continue
        (attempt,) = item.attempts
        result = await _acknowledge(questions[item.command.command_id], transport)
        await journal.record_attempt_fact(EventDraft(
            event_id=f"clarification-recovered:{item.command.command_id}", session_id=session_id,
            payload=CommandOutcomeReceived(item.command.command_id, attempt.attempt_id,
                                           CommandOutcome(OutcomeStatus.SUCCEEDED, value=result)),
            occurred_at=datetime.now(UTC), causation_id=attempt.started_event_id,
        ))


@dataclass(frozen=True, slots=True)
class DeadlineView:
    at: datetime
    plan: str
    reached: bool


@dataclass(frozen=True, slots=True)
class ClarificationView:
    question_id: str
    options: tuple[str, ...]
    answer: str | None
    deadline: DeadlineView | None = None

    @classmethod
    def from_event(cls, event, options, answer, reached):
        step = event.payload.step
        deadline = deadline_for(step)
        if deadline is None:
            return cls(step.step_id, options, answer)
        due_at = event.occurred_at + timedelta(seconds=deadline["after_seconds"])
        return cls(step.step_id, options, answer, DeadlineView(due_at, deadline["plan"], reached))
