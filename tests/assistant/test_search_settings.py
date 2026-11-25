import asyncio
import json

import httpx
from pydantic import ValidationError
import pytest

from konden.config import write_json
from konden.llm.config import initial_connections
from konden.model_settings import ModelConfigurationError
from konden.search import SearchConfig, SearchSettings


def test_search_preferences_are_separate_from_models_and_credentials(tmp_path):
    connections = initial_connections()
    connections["openai"]["api_key"] = "private-key"
    path = tmp_path / "connections.json"
    write_json(path, connections)
    config_path = tmp_path / "config.json"
    config_path.write_text('{"model":{"default":null,"candidates":[]}}', encoding="utf-8")
    before = path.read_bytes(), config_path.read_bytes()
    settings = SearchSettings(path)
    selected = SearchConfig(enabled=True, provider="openai", openai_model="openai/gpt-6.1-terra")
    settings.save(selected)
    settings.save(selected.model_copy(update={"enabled": False}))
    assert SearchSettings(path).config().model_dump() == {**selected.model_dump(), "enabled": False}
    assert before == (path.read_bytes(), config_path.read_bytes())
    assert "private-key" not in settings.path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("provider", "message"), [
    ("openai", r"openai\.api_key"), ("openai-chatgpt", "ChatGPT 订阅授权"),
])
def test_missing_connection_cannot_replace_saved_search_preferences(tmp_path, provider, message):
    path = tmp_path / "connections.json"
    connections = initial_connections()
    connections["openai-chatgpt"]["auth_file"] = str(tmp_path / "missing.json")
    write_json(path, connections)
    settings = SearchSettings(path)
    with pytest.raises(ModelConfigurationError, match=message):
        settings.save(SearchConfig(provider=provider, openai_model=f"{provider}/gpt-6.1-terra"))
    assert not settings.path.exists()
    with pytest.raises(ValidationError, match="搜索模型"):
        SearchConfig(provider=provider)


def test_subscription_search_uses_the_configured_grant_without_an_api_key(tmp_path, chatgpt_login):
    connections = initial_connections()
    auth_file = chatgpt_login()
    connections["openai-chatgpt"]["auth_file"] = str(auth_file)
    path = tmp_path / "connections.json"
    write_json(path, connections)
    settings = SearchSettings(path)
    model = "openai-chatgpt/gpt-6.2-sol"
    with pytest.raises(ValidationError, match="所选搜索服务"):
        SearchConfig(provider="openai", openai_model=model)
    selected = SearchConfig(provider="openai-chatgpt", openai_model=model)
    before = path.read_bytes(), auth_file.read_bytes()
    settings.save(selected)

    def handle(request):
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == "Bearer private-access"
        assert json.loads(request.content)["model"] == "gpt-6.2-sol"
        event = {"type": "response.completed", "response": {
            "status": "completed",
            "output": [
                {"type": "web_search_call", "action": {"sources": [{"type": "url", "url": "https://example.com/"}]}},
                {"type": "message", "content": [{"type": "output_text", "text": "answer"}]},
            ],
            "usage": {"input_tokens": 15, "output_tokens": 5},
        }}
        return httpx.Response(200, text=f"data: {json.dumps(event)}\n\n")

    result = asyncio.run(settings.search("question", settings.config(), transport=httpx.MockTransport(handle)))
    assert result["provider"] == "openai-chatgpt" and result["model"] == model
    assert result["searched"] and result["summary"] == "answer"
    assert [item["url"] for item in result["sources"]] == ["https://example.com/"]
    assert "private-access" not in settings.path.read_text(encoding="utf-8")
    assert (path.read_bytes(), auth_file.read_bytes()) == before


def test_deepseek_test_returns_real_sources_without_inventing_an_answer(tmp_path):
    connections = initial_connections()
    connections["deepseek"]["api_key"] = "private-key"
    path = tmp_path / "connections.json"
    write_json(path, connections)
    def handle(request):
        assert request.url == "https://api.deepseek.com/v1/tools/search"
        assert json.loads(request.content)["query"] == "question"
        return httpx.Response(200, json={"results": [{"title": "Source", "url": "https://example.com/",
            "snippet": "Original snippet", "summary": "summary", "content": "", "date": None,
            "site_name": None, "site_icon": None, "rerank_score": 0.5, "authority": 1}]})
    result = asyncio.run(SearchSettings(path).search("question", SearchConfig(), transport=httpx.MockTransport(handle)))
    assert result["searched"] and result["summary"] is None
    assert result["sources"] == [{"title": "Source", "url": "https://example.com/", "snippet": "Original snippet"}]
    assert not SearchSettings(path).path.exists()
