"""A clarification is an assistant reply with optional choices, answered once in its session."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from konden.runtime import Event, InvokeTool, ModelDecision, StepCommitted
from konden.runtime.model import argument_rejection, rejected_tool_arguments


ASK_USER = "ask_user"
CLARIFICATION_OPTIONS = "clarification_options"
ANSWER_SOURCE = "clarification_answer"
Nonblank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Question(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    question: Nonblank = Field(description="用户可以直接回答的一个具体问题，带上必要背景。")
    options: list[Nonblank] = Field(default_factory=list, description="可直接选择的简短答复。开放式问题可省略。")

    @field_validator("options")
    @classmethod
    def distinct(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values):
            raise ValueError("选项不能重复")
        return values


ASK_USER_SCHEMA = {"type": "function", "function": {
    "name": ASK_USER,
    "description": "在与用户的私聊中提出一个需要用户回答的问题，并结束本轮。用户可点选或自由回答。"
                   "缺少会影响下一步的关键信息时单独调用，问题只写在 question 中。收到答复后继续。",
    "parameters": Question.model_json_schema(),
}}


def clarification_reply(decision: ModelDecision) -> tuple[ModelDecision, list[str] | None]:
    calls = decision.command_requests
    if len(calls) != 1 or calls[0].name != ASK_USER or argument_rejection(calls[0]) is not None:
        return decision, None
    arguments = calls[0].argument_dict()
    try:
        question = Question.model_validate(arguments)
    except ValidationError as exc:
        errors = [f"{'.'.join(map(str, item['loc']))}: {item['msg']}" for item in exc.errors(include_url=False)]
        rejected = rejected_tool_arguments(
            json.dumps(arguments, ensure_ascii=False), code="VALIDATION_ERROR",
            error="；".join(errors), hint="修正问题和选项后重新提问。",
        )
        return ModelDecision(decision.content, (InvokeTool(ASK_USER, rejected),)), None
    preamble = decision.content.strip()
    text = question.question
    if preamble and preamble != text:
        text = f"{preamble}\n\n{text}"
    return ModelDecision(text), question.options


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


@dataclass(frozen=True, slots=True)
class ClarificationView:
    question_id: str
    options: tuple[str, ...]
    answer: str | None
