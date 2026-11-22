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


class CodexWork(A2AWork):
    codex_session_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$",
        description="沿用会话时填写从 list_a2a_bots 查到的会话 ID；开启新会话时省略。")


class DelegateA2AWork(CodexWork):
    bot_id: str = Field(pattern=r"^[0-9a-f]{32}$", description="从 list_a2a_bots 查到的目标 Bot ID。")


class KondenWork(A2AWork):
    recipient: str = Field(min_length=1, description="从 get_context 查到的 Konden Bot 名字。")
    delivery_id: str = Field(min_length=1, max_length=200,
        description="本次派单的唯一标识。结果未确认时，按原任务核对；新任务使用新标识。")


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
            LoadedTool(A2A_LIST, "查询可派单的 Codex Bot 和已关联的 Codex 会话，返回名称与对应 ID。",
                       {"type": "object", "properties": {}, "additionalProperties": False},
                       partial(self._call, A2A_LIST)),
            LoadedTool(A2A_DELEGATE,
                       "把用户交办的任务交给指定 A2A Bot。可选择一个已关联的 Codex 会话继续，也可开启新会话。"
                       "报告、附件和用于打开原生 Codex 会话的链接会显示在当前对话。"
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
