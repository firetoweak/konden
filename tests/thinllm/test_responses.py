import asyncio
import json

import httpx
import pytest

from thinllm import (
    InvalidLLMResponse,
    LLMAuthenticationError,
    LLMContextLengthError,
    LLMProviderError,
    ResponsesClient,
    create_client,
    resolve_endpoint,
)

REASONING = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque"}
CALL = {"type": "function_call", "call_id": "call_1", "name": "lookup", "arguments": '{"key":"answer"}'}


@pytest.fixture(params=["openai", "openai-chatgpt"])
def responses_connection(request, chatgpt_login):
    model = request.param + "/gpt-6.1-terra"
    connection = {"auth_file": str(chatgpt_login())} if request.param == "openai-chatgpt" else {"api_key": "existing-key"}
    header = "Bearer private-access" if request.param == "openai-chatgpt" else "Bearer existing-key"
    return resolve_endpoint(model, connection), model, header


def completed(output=None):
    return {"type": "response.completed", "response": {
        "status": "completed",
        "output": output if output is not None else [{"type": "message", "content": [
            {"type": "output_text", "text": "OK"},
        ]}],
        "usage": {"input_tokens": 120, "output_tokens": 5, "input_tokens_details": {"cached_tokens": 96}},
    }}


def sse(*events):
    return b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)


@pytest.mark.parametrize("searched", [False, True])
def test_openai_search_keeps_auto_choice_and_distinguishes_direct_answers(searched, responses_connection):
    endpoint, model, header = responses_connection
    output = [{"type": "message", "content": [{"type": "output_text", "text": "answer",
              "annotations": [{"type": "url_citation", "url": "https://example.com/", "title": "Source"}] if searched else []}]}]
    if searched:
        output.insert(0, {"type": "web_search_call", "action": {"type": "search",
                        "sources": [{"type": "url", "url": "https://example.com/"}]}})

    def handle(request):
        payload = json.loads(request.content)
        assert request.url == "https://api.openai.com/v1/responses"
        assert request.headers["Authorization"] == header
        assert payload["input"] == [{"role": "user", "content": "question"}]
        assert payload["store"] is False and payload["stream"] is True
        assert payload["tools"] == [{"type": "web_search"}]
        assert payload["tool_choice"] == "auto"
        assert payload["model"] == "gpt-6.1-terra"
        assert payload["include"] == ["web_search_call.action.sources"]
        # The subscription endpoint can omit output from response.completed even after delivering items.
        done_items = [
            {"type": "response.output_item.done", "output_index": index, "item": item}
            for index, item in enumerate(output)
        ]
        terminal = completed([] if endpoint.provider == "openai-chatgpt" else output)
        return httpx.Response(200, content=sse(*done_items, terminal))

    async def run():
        async with create_client(endpoint, transport=httpx.MockTransport(handle)) as client:
            result = await client.web_search("question", model)
        assert result == {"summary": "answer", "searched": searched,
                          "sources": [{"url": "https://example.com/", "title": "Source", "snippet": ""}] if searched else []}
    asyncio.run(run())


@pytest.mark.parametrize(("details", "cached", "written"), [
    (None, None, None),
    ({}, None, None),
    ({"cached_tokens": 0}, 0, None),
    ({"cached_tokens": 32, "cache_write_tokens": 64}, 32, 64),
])
def test_cache_usage_distinguishes_unknown_from_reported_counts(responses_connection, details, cached, written):
    endpoint, model, _ = responses_connection
    terminal = completed()
    usage = terminal["response"]["usage"]
    if details is None:
        del usage["input_tokens_details"]
    else:
        usage["input_tokens_details"] = details

    async def run():
        transport = httpx.MockTransport(lambda _: httpx.Response(200, content=sse(terminal)))
        async with create_client(endpoint, transport=transport) as client:
            result = await client.chat([{"role": "user", "content": "test"}], model)
        assert result.usage.cached_input_tokens == cached
        assert result.usage.cache_write_input_tokens == written

    asyncio.run(run())


def test_stream_and_tool_history_use_responses_without_losing_native_output(responses_connection):
    endpoint, model, header = responses_connection

    async def run():
        requests = []
        output = [{"type": "message", "role": "assistant", "phase": "commentary", "content": [
            {"type": "output_text", "text": "checking"},
        ]}, REASONING, CALL, {**CALL, "call_id": "call_2"}]
        responses = iter([
            sse({"type": "response.reasoning_summary_text.delta", "delta": "thinking"},
                {"type": "response.output_text.delta", "delta": "checking"},
                completed(output)),
            sse({"type": "response.output_text.delta", "delta": "OK"}, completed()),
        ])

        def handle(request):
            requests.append(request)
            return httpx.Response(200, content=next(responses))

        tools = [{"type": "function", "function": {"name": "lookup", "parameters": {
            "type": "object", "properties": {"key": {"type": "string"}, "optional": {"type": "string"}},
            "required": ["key"],
        }}}]
        history = [{"role": "system", "content": "help"}, {"role": "user", "content": [
            {"type": "text", "text": "read"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA==", "detail": "low"}},
        ]}]
        content, reasoning = [], []
        async with create_client(endpoint, transport=httpx.MockTransport(handle)) as client:
            assert isinstance(client, ResponsesClient)
            first = await client.chat(history, model, tools, on_content_delta=content.append,
                                      on_reasoning_delta=reasoning.append, reasoning_effort="high")
            assert first.response.content == "checking"
            assert content == ["checking"] and reasoning == ["thinking"]
            assert first.usage.cached_input_tokens == 96
            assert [call.id for call in first.response.calls] == ["call_1", "call_2"]
            assert first.response.message_extensions["responses_output"] == output
            second = await client.chat([*history, {
                "role": "assistant", "content": first.response.content, **first.response.message_extensions,
                "tool_calls": [{"id": "command_" + call.id, "function": {"name": call.name, "arguments": call.arguments}}
                               for call in first.response.calls],
            }, *[{"role": "tool", "tool_call_id": "command_" + call.id, "content": "42"}
                 for call in first.response.calls]], model, tools, tool_choice="required")
        assert second.response.content == "OK"
        assert str(requests[0].url) == "https://api.openai.com/v1/responses"
        assert requests[0].headers["authorization"] == header
        payload = json.loads(requests[0].content)
        assert payload["instructions"] == "help" and payload["model"] == model.partition("/")[2]
        assert payload["store"] is False and payload["stream"] is True
        assert "messages" not in payload and "stream_options" not in payload
        assert payload["reasoning"] == {"effort": "high", "summary": "auto"}
        functions = [{"type": "function", "strict": False, **tools[0]["function"]}]
        replay = json.loads(requests[1].content)
        assert replay["input"][:len(payload["input"])] == payload["input"]
        if endpoint.provider == "openai-chatgpt":
            assert payload["input"][0] == {"type": "additional_tools", "role": "developer", "tools": functions}
            assert "tools" not in payload
        else:
            assert payload["tools"] == functions
        assert payload["input"][-1]["content"][1] == {
            "type": "input_image", "image_url": "data:image/png;base64,AA==", "detail": "low",
        }
        assert payload["tool_choice"] == "auto"
        assert replay["tool_choice"] == "required"
        assert "reasoning" not in replay
        assert replay["input"][len(payload["input"]):] == [
            *output[:2],
            {**CALL, "call_id": "command_call_1"}, {**CALL, "call_id": "command_call_2"},
            {"type": "function_call_output", "call_id": "command_call_1", "output": "42"},
            {"type": "function_call_output", "call_id": "command_call_2", "output": "42"},
        ]

    asyncio.run(run())


def test_replayed_control_call_does_not_create_an_unpaired_tool_call(responses_connection):
    endpoint, model, _ = responses_connection

    async def run():
        requests = []

        def handle(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, content=sse(completed()))

        history = [{"role": "assistant", "content": "Waiting for confirmation.",
                    "responses_output": [REASONING, CALL]}]
        async with create_client(endpoint, transport=httpx.MockTransport(handle)) as client:
            await client.chat(history, model)
        assert requests[0]["input"] == [REASONING, {"role": "assistant", "content": [
            {"type": "output_text", "text": "Waiting for confirmation."},
        ]}]

    asyncio.run(run())


@pytest.mark.parametrize("body", [
    sse({"type": "response.output_text.delta", "delta": "partial"}),
    sse({"type": "response.incomplete", "response": {"incomplete_details": {"reason": "max_output_tokens"}}}),
    sse({"type": "response.completed", "response": {"status": "completed", "output": []}}),
    sse({"type": "response.output_text.delta", "delta": 42}),
    sse({"type": "response.completed", "response": {
        "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}],
        "usage": {"input_tokens": 1, "output_tokens": 1, "input_tokens_details": []},
    }}),
    b"data: {\n\n", b"data: [DONE]\n\n",
])
def test_incomplete_or_malformed_stream_is_never_a_success(body, responses_connection):
    endpoint, model, _ = responses_connection

    async def run():
        async with create_client(endpoint, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
            with pytest.raises(InvalidLLMResponse):
                await client.chat([], model)

    asyncio.run(run())


@pytest.mark.parametrize(("status", "body", "error"), [
    (401, b'{"error":{"message":"expired"}}', LLMAuthenticationError),
    (200, sse({"type": "response.failed", "response": {"error": {
        "code": "context_length_exceeded", "message": "too long",
    }}}), LLMContextLengthError),
    (200, sse({"type": "error", "code": "server_error", "message": "failed"}), LLMProviderError),
])
def test_upstream_errors_keep_the_existing_error_contract(status, body, error, responses_connection):
    endpoint, model, _ = responses_connection

    async def run():
        async with create_client(endpoint, transport=httpx.MockTransport(lambda _: httpx.Response(status, content=body))) as client:
            with pytest.raises(error):
                await client.chat([], model)

    asyncio.run(run())


@pytest.mark.parametrize("streaming", [False, True])
def test_subscription_limit_explains_usage_without_retrying(chatgpt_login, streaming):
    """Quota exhaustion can arrive before the stream or as its terminal event."""
    error = {"code": "subscription_sharing_usage_limit_exceeded", "message": "limit reached"}
    attempts = []

    def handle(request):
        attempts.append(request.url)
        if streaming:
            return httpx.Response(200, content=sse({"type": "response.failed", "response": {"error": error}}))
        return httpx.Response(429, json={"error": error})

    async def run():
        model = "openai-chatgpt/gpt-6.2-sol"
        endpoint = resolve_endpoint(model, {"auth_file": str(chatgpt_login())})
        async with create_client(endpoint, transport=httpx.MockTransport(handle)) as client:
            with pytest.raises(LLMProviderError, match=r"https://chatgpt\.com/settings/usage"):
                await client.chat([], model)

    asyncio.run(run())
    assert len(attempts) == 1


def test_callback_errors_are_not_reclassified_as_upstream_corruption(responses_connection):
    endpoint, model, _ = responses_connection

    async def run():
        error = TypeError("caller bug")

        def callback(_):
            raise error

        body = sse({"type": "response.output_text.delta", "delta": "hello"})
        async with create_client(endpoint, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
            with pytest.raises(TypeError) as caught:
                await client.chat([], model, on_content_delta=callback)
        assert caught.value is error

    asyncio.run(run())
