"""The Bot-facing directory and input contract for A2A delegation."""

from __future__ import annotations

from functools import partial
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from konden.assistant.file_attachments import validate_attachment_name
from konden.assistant.toolsets import LoadedTool, ToolsetDescriptor

A2A_LIST = "list_a2a_bots"
A2A_DELEGATE = "delegate_a2a_task"
A2A_COMPLETED = "A2A_TASK_COMPLETED"
A2A_TOOLSET = ToolsetDescriptor("a2a:codex", "查询已创建的 Codex Bot，并向它委派任务和接收文件成果。")


class A2AWork(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    brief: str = Field(min_length=1, max_length=16000, description="交代任务目标、数据和验收要求，仅提供本次工作所需信息。")
    output_files: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=4,
        description="要交付的文件名，例如 chart.png、data.csv。最多四个，只填文件名；仅需报告时省略。",
    )

    @field_validator("brief")
    @classmethod
    def nonblank_brief(cls, value):
        if not value.strip():
            raise ValueError("任务要求不能为空白")
        return value

    @field_validator("output_files")
    @classmethod
    def valid_file_names(cls, names):
        for name in names:
            validate_attachment_name(name)
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError("交付文件名不能重复")
        return names


class DelegateA2AWork(A2AWork):
    bot_id: str = Field(pattern=r"^[0-9a-f]{32}$", description="从 list_a2a_bots 查到的目标 Bot ID。")


class A2AToolset:
    def __init__(self, transport, session_id):
        self.transport = transport
        self.session_id = session_id

    def descriptors(self):
        return (A2A_TOOLSET,)

    def handles(self, toolset_id):
        return toolset_id == A2A_TOOLSET.id

    async def load(self, toolset_id, revision):
        return (
            LoadedTool(A2A_LIST, "查询在 Konden 中创建的 A2A Bot，返回名字和派单所需的 Bot ID。",
                       {"type": "object", "properties": {}, "additionalProperties": False},
                       partial(self._call, A2A_LIST)),
            LoadedTool(A2A_DELEGATE,
                       "把用户交办的任务交给指定 A2A Bot。Codex 在任务独立目录工作。"
                       "等待结果，报告和文件会直接展示在当前对话，无需另用 send_file。"
                       "若结果未确认，先告诉用户，避免重复派单。",
                       DelegateA2AWork.model_json_schema(), partial(self._call, A2A_DELEGATE),
                       requires_authorization=True),
        )

    def restore(self, toolset_id, revision, tools):
        return tuple(LoadedTool(tool.name, tool.description, dict(tool.parameters), partial(self._call, tool.name),
                                tool.requires_authorization, tool.provider_data) for tool in tools)

    async def _call(self, operation, arguments):
        if operation == A2A_DELEGATE:
            try:
                arguments = DelegateA2AWork.model_validate(dict(arguments)).model_dump()
            except ValidationError as error:
                return {"ok": False, "code": "INVALID_ARGUMENT", "error": str(error)}
        elif arguments:
            return {"ok": False, "code": "INVALID_ARGUMENT", "error": "list_a2a_bots 不接受参数。"}
        return await self.transport(operation, self.session_id, arguments)
