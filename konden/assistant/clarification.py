"""A clarification is a delivered question, answered once in its session."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
)

from konden.assistant.delivery import emit_delivery
from konden.automation.once import MAX_DELAY_SECONDS
from konden.runtime import (
    AgentRuntime,
    CommandOutcome,
    CommandOutcomeReceived,
    CommandPhase,
    DomainFactCommitted,
    Event,
    EventDraft,
    OutcomeStatus,
    UserMessageReceived,
    replay,
)
from konden.runtime.dispatcher import ToolBinding

ASK_USER = "ask_user"
QUESTION_SENT = "clarification.question_sent"
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


def ask_user_schema(model: type[Question]):
    return {"type": "function", "function": {
        "name": ASK_USER, "parameters": model.model_json_schema(),
        "description": "在私聊中发一张可以点选或自由回答的澄清卡片。必要的引导写在卡片里，同批消息和卡片的展示顺序不保证。发出后可继续做不依赖答案的工作；没有其他能做的事时，可和 rest 同批调用。",
    }}


def questions_by_id(events: tuple[Event, ...]) -> dict[str, Event]:
    return {
        event.delivery.delivery_id: event
        for event in events
        if isinstance(event.payload, DomainFactCommitted) and event.payload.fact_type == QUESTION_SENT
    }


def is_retired_question_id(question_id: str) -> bool:
    """References from before the cutover used the persisted Step ID."""
    return question_id.startswith("step_")


def question_text(event: Event) -> str:
    data = event.payload.data
    return "\n".join((data["question"], *(f"- {option}" for option in data["options"])))


def has_answer(events, question_id: str) -> bool:
    return any(isinstance(e.payload, UserMessageReceived) and e.delivery is not None
               and e.delivery.source == ANSWER_SOURCE and e.delivery.delivery_id == question_id for e in events)


async def _complete_delivery(event: Event, transport, sink):
    question_id = event.delivery.delivery_id
    if sink is not None:
        await emit_delivery(sink, event.session_id, question_id, event.payload.data["question"])
    deadline = event.payload.data.get("deadline")
    result = {"question_id": question_id, "message": "问题已发出。继续处理不依赖答案的工作。只剩待答事项时就休息，答复到达会叫醒你。"}
    if deadline is not None:
        registered = await transport("schedule_once", event.session_id, {
            "schedule_id": SCHEDULE_PREFIX + question_id, "delay_seconds": deadline["after_seconds"],
            "started_at": event.occurred_at.isoformat(), "purpose": deadline["plan"],
        })
        result.update(wait_until=registered["due_at"], plan=deadline["plan"])
    return {"ok": True, "code": "QUESTION_SENT", "data": result}


def clarification_binding(journal, model: type[Question], transport, sink):
    runtime = AgentRuntime(journal, None, {})

    async def handler(context, arguments):
        try:
            question = model.model_validate(arguments)
        except ValidationError as exc:
            errors = [f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors(include_url=False)]
            return {"ok": False, "code": "VALIDATION_ERROR", "error": "；".join(errors),
                    "hint": "修正问题和选项后重新提问。"}
        event = await runtime.receive_domain_fact(
            context.session_id, QUESTION_SENT, question.model_dump(exclude_none=True),
            source=ASK_USER, delivery_id=context.command_id,
        )
        return await _complete_delivery(event, transport, sink)
    return ToolBinding(handler)


async def recover_clarification_attempts(journal, session_id, transport, sink):
    events = await journal.snapshot(session_id)
    questions = questions_by_id(events)
    if not questions:
        return
    for item in replay(session_id, events).state.commands:
        if item.phase is not CommandPhase.UNKNOWN or item.command.command_id not in questions:
            continue
        (attempt,) = item.attempts
        result = await _complete_delivery(questions[item.command.command_id], transport, sink)
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
    def from_event(cls, event: Event, answer: str | None, reached: bool):
        question_id = event.delivery.delivery_id
        options = tuple(event.payload.data["options"])
        deadline = event.payload.data.get("deadline")
        if deadline is None:
            return cls(question_id, options, answer)
        due_at = event.occurred_at + timedelta(seconds=deadline["after_seconds"])
        return cls(question_id, options, answer, DeadlineView(due_at, deadline["plan"], reached))
