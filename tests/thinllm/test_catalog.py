import asyncio
import errno
import json
import socket
import ssl

import httpx
import pytest

from thinllm import InvalidLLMResponse, LLMAuthenticationError, LLMProviderError, LLMTransientError, list_codex_models
from thinllm import list_models


def model(slug="MixedCase-ID", **overrides):
    return {"slug": slug, "display_name": "Display name", "visibility": "list", "context_window": 500000,
            "default_reasoning_level": "low", "supported_reasoning_levels": [{"effort": "low", "description": "Light"},
                                                                           {"effort": "high", "description": "Deep"}],
            "base_instructions": "must not be exposed", **overrides}


@pytest.fixture
def auth_file(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"tokens": {"access_token": "private-access", "account_id": "account"}}), encoding="utf-8")
    return path


def test_lists_account_models_without_hidden_entries_or_prompt_metadata(auth_file):
    before = auth_file.read_bytes()
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url).startswith("https://chatgpt.com/backend-api/codex/models?")
        assert request.url.params["client_version"]
        assert request.headers["Authorization"] == "Bearer private-access"
        assert request.headers["ChatGPT-Account-Id"] == "account"
        return httpx.Response(200, json={"models": [model(), model("hidden", visibility="hide"),
                                                   model("unknown-capacity", context_window=None,
                                                         supported_reasoning_levels=[], default_reasoning_level=None)]})

    result = asyncio.run(list_codex_models({"auth_file": str(auth_file)}, transport=httpx.MockTransport(handle)))
    assert len(requests) == 1
    assert result == [{"slug": "MixedCase-ID", "display_name": "Display name", "context_window": 500000,
                       "default_reasoning_level": "low", "supported_reasoning_levels": [{"effort": "low"}, {"effort": "high"}]},
                      {"slug": "unknown-capacity", "display_name": "Display name", "context_window": None,
                       "default_reasoning_level": None, "supported_reasoning_levels": []}]
    assert auth_file.read_bytes() == before


@pytest.mark.parametrize("provider", ["openai", "openai-codex"])
@pytest.mark.parametrize(("status", "error", "reason", "action"), [
    (401, LLMAuthenticationError, "认证未通过", "connections.json"),
    (403, LLMAuthenticationError, "拒绝访问", "账号权限"),
    (408, LLMTransientError, "请求超时", "网络或代理"),
    (425, LLMTransientError, "暂不可用", "稍后重新加载"),
    (429, LLMTransientError, "请求受限", "配额或额度"),
    (500, LLMTransientError, "服务异常", "联系服务方"),
    (502, LLMTransientError, "服务异常", "联系服务方"),
    (503, LLMTransientError, "服务异常", "联系服务方"),
    (504, LLMTransientError, "请求超时", "网络或代理"),
    (400, LLMProviderError, "读取", "核对供应商连接设置"),
])
def test_provider_failure_identifies_reason_and_recovery_without_secrets(auth_file, provider, status, error, reason, action):
    """Avoid sending users to rotate keys for network/permission failures or blindly retry a rejected key."""
    transport = httpx.MockTransport(lambda _: httpx.Response(status, text="private-response"))
    with pytest.raises(error) as caught:
        asyncio.run(list_models(provider, {"auth_file": str(auth_file), "api_key": "private-key"}, transport=transport))
    message = str(caught.value)
    assert reason in message and action in message and f"HTTP {status}" in message
    assert "private-response" not in message and "private-key" not in message
    if status == 401:
        assert ("--login" if provider == "openai-codex" else "openai.api_key") in message


@pytest.mark.parametrize("payload", [{}, {"models": {}}, {"models": [model(context_window=True)]},
                                     {"models": [model(supported_reasoning_levels=[{"effort": 1}])]}])
def test_malformed_catalog_has_a_protocol_error(auth_file, payload):
    with pytest.raises(InvalidLLMResponse, match="模型列表格式"):
        asyncio.run(list_codex_models({"auth_file": str(auth_file)}, transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=payload))))


def test_new_request_reads_new_credentials_and_catalog_without_local_cache(auth_file):
    tokens = []

    def handle(request):
        tokens.append(request.headers["authorization"])
        return httpx.Response(200, json={"models": [model(f"model-{len(tokens)}")]})

    async def run():
        settings = {"auth_file": str(auth_file)}
        transport = httpx.MockTransport(handle)
        first = await list_codex_models(settings, transport=transport)
        auth_file.write_text(json.dumps({"tokens": {"access_token": "rotated", "account_id": "other-account"}}), encoding="utf-8")
        second = await list_codex_models(settings, transport=transport)
        assert first[0]["slug"] == "model-1" and second[0]["slug"] == "model-2"

    asyncio.run(run())
    assert tokens == ["Bearer private-access", "Bearer rotated"]


@pytest.mark.parametrize("provider", ["openai", "openai-codex"])
@pytest.mark.parametrize(("error", "reason", "action"), [
    (httpx.ConnectTimeout, "请求超时", "网络或代理"),
    (httpx.ReadTimeout, "请求超时", "网络或代理"),
    (httpx.ProxyError, "代理连接失败", "代理地址、认证和可用性"),
    (httpx.ConnectError, "网络通信失败", "网络、代理或证书"),
    (httpx.ReadError, "网络通信失败", "网络、代理或证书"),
    (httpx.RemoteProtocolError, "不符合 HTTP 协议", "下方原因"),
    (httpx.LocalProtocolError, "请求格式或地址协议错误", "密钥格式和服务地址"),
])
def test_transport_failures_do_not_claim_credentials_are_invalid(auth_file, provider, error, reason, action):
    """A missing catalog response cannot establish whether the supplied credential is valid."""
    def fail(request):
        credential = "private-access" if provider == "openai-codex" else "private-key"
        raise error(f"connection failed with {credential}", request=request)

    with pytest.raises(LLMTransientError) as caught:
        asyncio.run(list_models(provider, {"auth_file": str(auth_file), "api_key": "private-key"},
                                transport=httpx.MockTransport(fail)))
    message = str(caught.value)
    credential = "登录凭据" if provider == "openai-codex" else "密钥"
    assert reason in message and action in message
    assert f"无法判断{credential}是否有效" in message
    assert "connection failed with" in message
    assert "private-access" not in message and "private-key" not in message
    assert "请求：GET https://" in message and error.__name__ in message
    assert isinstance(caught.value.__cause__, error)


@pytest.mark.parametrize(("cause", "reason", "detail"), [
    (ssl.SSLCertVerificationError(1, "certificate verify failed: self-signed certificate"),
     "HTTPS 证书校验失败", "self-signed certificate"),
    (socket.gaierror(socket.EAI_NONAME, "getaddrinfo failed"), "域名解析失败", "getaddrinfo failed"),
    (ConnectionRefusedError(errno.ECONNREFUSED, "Connection refused"), "连接被拒绝", "Connection refused"),
    (ExceptionGroup("multiple connection attempts failed", [
        ConnectionRefusedError(errno.ECONNREFUSED, "IPv4 refused"),
        ConnectionRefusedError(errno.ECONNREFUSED, "IPv6 refused")]), "连接被拒绝", "IPv6 refused"),
])
def test_catalog_explains_the_actual_transport_cause(cause, reason, detail):
    """httpx's generic ConnectError must not hide DNS, TLS or socket errors behind another retry hint."""
    def fail(request):
        raise httpx.ConnectError("All connection attempts failed", request=request) from cause

    with pytest.raises(LLMTransientError) as caught:
        asyncio.run(list_models("openai", {"api_key": "private-key"}, transport=httpx.MockTransport(fail)))
    message = str(caught.value)
    assert reason in message and detail in message
    assert "https://api.openai.com/v1/models" in message
    assert "ConnectError" in message and "private-key" not in message


def test_transport_diagnostics_redact_credentials_and_url_secrets():
    from thinllm import describe_transport_error

    request = httpx.Request("GET", "https://url-user:url-password@api.example/models?key=query-secret", headers={
        "Authorization": "Bearer header-secret", "ChatGPT-Account-Id": "account-secret"})
    error = httpx.ProxyError(
        f"407 Proxy Authentication Required; {request.url}; http://proxy-user:proxy-password@proxy.example; "
        "Bearer header-secret; Basic proxy-encoded-secret; account-secret", request=request)
    message = describe_transport_error(error)
    assert "407 Proxy Authentication Required" in message
    assert "GET https://api.example/models" in message
    assert "proxy.example" in message
    for secret in ("url-user", "url-password", "query-secret", "header-secret", "account-secret",
                   "proxy-user", "proxy-password", "proxy-encoded-secret"):
        assert secret not in message


def test_suppressed_socket_context_is_retained_in_diagnostics():
    """A real Windows refused connection has errno 22/WinError 1225, hidden by the HTTP transport."""
    def fail(request):
        try:
            raise ConnectionRefusedError(22, "remote computer refused the network connection")
        except ConnectionRefusedError:
            raise httpx.ConnectError("All connection attempts failed", request=request) from None

    with pytest.raises(LLMTransientError) as caught:
        asyncio.run(list_models("openai", {"api_key": "private-key"}, transport=httpx.MockTransport(fail)))
    assert "连接被拒绝" in str(caught.value)
    assert "ConnectionRefusedError" in str(caught.value)
    assert "remote computer refused the network connection" in str(caught.value)


def test_invalid_authorization_header_is_not_exposed_by_protocol_diagnostics():
    from thinllm import describe_transport_error

    request = httpx.Request("GET", "https://api.example/models", headers={"Authorization": "Bearer first\nsecond"})
    error = httpx.LocalProtocolError("Illegal header value b'Bearer first\\nsecond'", request=request)
    message = describe_transport_error(error)
    assert "请求格式或地址协议错误" in message and "LocalProtocolError" in message
    assert "first" not in message and "second" not in message


def test_corrupt_credentials_and_unknown_errors_propagate(auth_file):
    def fail(request):
        raise RuntimeError("internal failure")

    for provider in ("openai", "openai-codex"):
        with pytest.raises(RuntimeError, match="internal failure"):
            asyncio.run(list_models(provider, {"auth_file": str(auth_file), "api_key": "private-key"},
                                    transport=httpx.MockTransport(fail)))
    auth_file.write_text("{", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(list_codex_models({"auth_file": str(auth_file)}, transport=httpx.MockTransport(fail)))


@pytest.mark.parametrize(("provider", "entry", "levels", "default"), [
    ("deepseek", {"id": "deepseek-flash", "name": "DeepSeek Flash", "context_window": 1048576,
                  "effort": {"supported_levels": ["low", "high", "max"], "default_level": "high"}},
     ["low", "high", "max"], "high"),
    ("stepfun", {"id": "step-5-preview", "reasoning_effort_support_list": ["low", "medium", "high"]},
     ["low", "medium", "high"], None),
    ("openai", {"id": "gpt-6.1-sol"}, ["low", "medium", "high", "xhigh", "max"], "medium"),
    ("qwen", {"id": "unknown-new-model"}, [], None),
])
def test_native_model_capabilities_are_not_replaced_by_provider_wide_tiers(provider, entry, levels, default):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [entry]}))
    result = asyncio.run(list_models(provider, {"api_key": "private"}, transport=transport))
    assert result[0]["reasoning_levels"] == levels
    assert result[0]["default_reasoning_effort"] == default
    assert result[0]["model"] == provider + "/" + entry["id"]
    assert "private" not in repr(result)


def test_openai_catalog_excludes_non_conversation_and_retired_models():
    entries = [{"id": "gpt-6.1-terra"}, {"id": "gpt-image-2"}, {"id": "text-embedding-3-small"},
               {"id": "gpt-4o-mini-transcribe"}, {"id": "gpt-5-search-api"},
               {"id": "gpt-4o", "shutdown_date": "2020-01-01"}]
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"data": entries}))
    result = asyncio.run(list_models("openai", {"api_key": "private"}, transport=transport))
    assert [item["model"] for item in result] == ["openai/gpt-6.1-terra"]


def test_invalid_native_levels_are_a_catalog_protocol_error():
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json={"data": [
        {"id": "deepseek-flash", "effort": {"supported_levels": [1]}}]}))
    with pytest.raises(InvalidLLMResponse):
        asyncio.run(list_models("deepseek", {"api_key": "private"}, transport=transport))
