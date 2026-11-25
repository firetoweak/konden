"""Search configuration and execution, reusing provider connections."""
from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, model_validator

from konden.config import write_json
from konden.llm.config import load_connections
from konden.model_settings import ModelConfigurationError
from konden.search.deepseek import (
    BASE_URL,
    DeepSeekAuth,
    MissingApiKey,
    SearchInput,
    request_web,
)
from thinllm import MissingProviderSetting, create_client, resolve_endpoint

SearchProvider = Literal["deepseek", "openai", "openai-chatgpt"]


class SearchConfig(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    enabled: bool = True
    provider: SearchProvider = "deepseek"
    openai_model: str | None = None

    @model_validator(mode="after")
    def selected_search_model(self):
        model = self.openai_model
        if model is None:
            if self.enabled and self.provider != "deepseek":
                raise ValueError("请选择搜索模型")
            return self
        provider, _, name = model.partition("/")
        if provider not in ("openai", "openai-chatgpt") or not name.strip() or model != model.strip():
            raise ValueError("请选择有效的搜索模型")
        if self.provider != "deepseek" and provider != self.provider:
            raise ValueError("搜索模型必须属于所选搜索服务")
        return self


class SearchSource(BaseModel):
    model_config = ConfigDict(strict=True)
    title: str
    url: str
    snippet: str


class SearchResult(BaseModel):
    model_config = ConfigDict(strict=True)
    query: str
    provider: SearchProvider
    model: str | None
    elapsed_ms: int
    searched: bool
    summary: str | None
    sources: list[SearchSource]


def search_path(connections_path: Path) -> Path:
    return connections_path.parent / "search.json"


def load_search_config(path: Path) -> SearchConfig:
    return SearchConfig() if not path.exists() else SearchConfig.model_validate_json(path.read_bytes())


class SearchSettings:
    def __init__(self, connections_path: Path):
        self.connections_path = connections_path
        self.path = search_path(connections_path)

    def config(self) -> SearchConfig:
        return load_search_config(self.path)

    def require_connection(self, config: SearchConfig):
        connections = load_connections(self.connections_path)
        try:
            return resolve_endpoint(f"{config.provider}/search", connections[config.provider])
        except MissingProviderSetting as error:
            if config.provider == "openai-chatgpt":
                raise ModelConfigurationError("请先在「供应商与模型」完成 ChatGPT 订阅授权。") from error
            raise ModelConfigurationError(f"请配置 {config.provider}.{error.setting}") from error

    def save(self, config: SearchConfig) -> dict:
        if config.enabled:
            self.require_connection(config)
        write_json(self.path, config.model_dump())
        return config.model_dump()

    async def search(self, query: str, config: SearchConfig, *, transport=None) -> dict:
        if not config.enabled:
            raise ModelConfigurationError("请先启用网络搜索")
        endpoint = self.require_connection(config)
        started = perf_counter()
        if config.provider != "deepseek":
            async with create_client(endpoint, transport=transport) as client:
                result = await client.web_search(query, config.openai_model)
        else:
            arguments = SearchInput(query=query)
            async with httpx.AsyncClient(base_url=BASE_URL, auth=DeepSeekAuth(self.connections_path),
                                        timeout=30, transport=transport) as client:
                try:
                    response = await request_web(client, "search", arguments)
                except MissingApiKey as error:
                    raise ModelConfigurationError(str(error)) from error
            result = {"summary": None, "searched": True,
                      "sources": [{"title": item["title"], "url": item["url"],
                                   "snippet": item["snippet"] or item["summary"]} for item in response["results"]]}
        return {"query": query, "provider": config.provider,
                "model": config.openai_model if config.provider != "deepseek" else None,
                "elapsed_ms": round((perf_counter() - started) * 1000), **result}
