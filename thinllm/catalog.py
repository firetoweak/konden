"""Model choices and capabilities available to the configured connections."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from thinllm.auth import ChatGPTAuth
from thinllm.errors import (
    ChatGPTReauthorizationRequired,
    InvalidLLMResponse,
    LLMAuthenticationError,
    LLMProviderError,
    LLMTransientError,
)
from thinllm.http_errors import describe_transport_error
from thinllm.model_metadata import openai_conversation_model, openai_metadata
from thinllm.providers import resolve_endpoint

_CHATGPT = "ChatGPT 订阅"


def _catalog_transport_error(provider: str, error: httpx.TransportError) -> LLMTransientError:
    credential = "登录凭据" if provider == _CHATGPT else "密钥"
    return LLMTransientError(
        f"{provider} 模型目录读取失败：{describe_transport_error(error)}\n"
        f"未取得供应商的完整 HTTP 响应，无法判断{credential}是否有效。")


def _check_catalog_response(provider: str, response: httpx.Response) -> None:
    status = response.status_code
    if status == 200:
        return
    if status == 401:
        message = f"{provider} 模型目录认证未通过（HTTP 401）。"
        if provider == _CHATGPT:
            raise ChatGPTReauthorizationRequired(message + "请重新授权。")
        raise LLMAuthenticationError(
            message + f"请检查 connections.json 中的 {provider}.api_key，确认密钥正确且未失效后重新加载。")
    if status == 403:
        raise LLMAuthenticationError(
            f"{provider} 拒绝访问模型目录（HTTP 403）。请检查账号权限、服务地区限制或代理的访问策略。")
    if status in (408, 504):
        raise LLMTransientError(f"{provider} 模型目录请求超时（HTTP {status}）。请稍后重新加载；若持续超时，请检查网络或代理。")
    if status == 429:
        raise LLMTransientError(
            f"{provider} 模型目录请求受限（HTTP 429）。请稍后重新加载；若持续出现，请检查供应商账号的配额或额度。")
    if status in (500, 502, 503):
        raise LLMTransientError(f"{provider} 模型目录服务异常（HTTP {status}）。请稍后重新加载；若持续出现，请联系服务方。")
    if status == 425:
        raise LLMTransientError(f"{provider} 模型目录暂不可用（HTTP 425）。请稍后重新加载。")
    raise LLMProviderError(f"读取 {provider} 模型目录失败（HTTP {status}）。请核对供应商连接设置，或联系服务方。")


class _ReasoningLevel(BaseModel):
    model_config = ConfigDict(strict=True)
    effort: str = Field(min_length=1)


class _ChatGPTModel(BaseModel):
    model_config = ConfigDict(strict=True)
    slug: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    context_window: int | None = Field(default=None, gt=0)
    default_reasoning_level: str | None = None
    supported_reasoning_levels: list[_ReasoningLevel] = Field(default_factory=list)
    visibility: str = Field(min_length=1)


class _ChatGPTCatalog(BaseModel):
    model_config = ConfigDict(strict=True)
    models: list[_ChatGPTModel]


async def _chatgpt_models(endpoint, transport: httpx.AsyncBaseTransport | None) -> list[dict]:
    try:
        async with httpx.AsyncClient(
            base_url=endpoint.base_url + "/", auth=ChatGPTAuth(endpoint.auth_file), timeout=30, transport=transport,
        ) as client:
            response = await client.get("models")
    except httpx.TransportError as error:
        raise _catalog_transport_error(_CHATGPT, error) from error
    _check_catalog_response(_CHATGPT, response)
    try:
        catalog = _ChatGPTCatalog.model_validate_json(response.content)
    except ValidationError as error:
        raise InvalidLLMResponse("invalid_model_catalog", f"{_CHATGPT}模型列表格式不符合约定。") from error
    return [
        {"model": endpoint.provider + "/" + item.slug, "name": item.display_name,
         "context_window": item.context_window, "reasoning_levels": [level.effort for level in item.supported_reasoning_levels],
         "default_reasoning_effort": item.default_reasoning_level, "web_search": False}
        for item in catalog.models if item.visibility == "list"
    ]


class _Effort(BaseModel):
    model_config = ConfigDict(strict=True)
    supported_levels: list[str]
    default_level: str | None = None


class _ApiModel(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str = Field(min_length=1)
    name: str | None = None
    context_window: int | None = Field(default=None, gt=0)
    max_input_tokens: int | None = Field(default=None, gt=0)
    effort: _Effort | None = None
    reasoning_effort_support_list: list[str] | None = None
    supported_protocols: list[str] | None = None
    output_modalities: list[str] | None = None
    shutdown_date: str | None = None

    @field_validator("shutdown_date")
    @classmethod
    def shutdown_day(cls, value):
        if value is not None:
            date.fromisoformat(value)
        return value


class _ApiCatalog(BaseModel):
    model_config = ConfigDict(strict=True)
    data: list[_ApiModel]


async def list_models(provider: str, connection: Mapping[str, str], *,
                      transport: httpx.AsyncBaseTransport | None = None) -> list[dict]:
    """Discover account-visible conversation models and native reasoning levels."""
    endpoint = resolve_endpoint(provider + "/catalog", connection)
    if endpoint.auth_file is not None:
        return await _chatgpt_models(endpoint, transport)
    headers = {} if endpoint.api_key is None else {"Authorization": f"Bearer {endpoint.api_key}"}
    try:
        async with httpx.AsyncClient(base_url=endpoint.base_url + "/", headers=headers,
                                    transport=transport, timeout=30) as client:
            response = await client.get("models")
    except httpx.TransportError as error:
        raise _catalog_transport_error(provider, error) from error
    _check_catalog_response(provider, response)
    try:
        catalog = _ApiCatalog.model_validate_json(response.content)
    except ValidationError as error:
        raise InvalidLLMResponse("invalid_model_catalog", f"{provider} 模型目录格式不符合约定。") from error
    result = []
    for item in catalog.data:
        if item.shutdown_date is not None and date.fromisoformat(item.shutdown_date) <= datetime.now().astimezone().date():
            continue
        if item.output_modalities is not None and "text" not in item.output_modalities:
            continue
        if item.supported_protocols is not None and "chat" not in item.supported_protocols:
            continue
        if provider == "openai" and not openai_conversation_model(item.id):
            continue
        if provider == "stepfun" and item.id.startswith("stepaudio"):
            continue
        metadata = {"context_window": item.context_window or item.max_input_tokens,
                    "reasoning_levels": [], "default_reasoning_effort": None, "web_search": False}
        if provider == "openai":
            metadata.update(openai_metadata(item.id))
        if item.effort is not None:
            metadata.update(reasoning_levels=item.effort.supported_levels,
                            default_reasoning_effort=item.effort.default_level)
        elif item.reasoning_effort_support_list is not None:
            metadata["reasoning_levels"] = item.reasoning_effort_support_list
        result.append({"model": provider + "/" + item.id, "name": item.name or item.id, **metadata})
    return sorted(result, key=lambda item: item["model"])
