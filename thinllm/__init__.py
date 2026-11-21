"""Thin streaming clients for built-in model providers.

Stays thin: no cross-provider fallback, load balancing, circuit breaking,
cost tracking, or user-defined providers.
"""

from thinllm.catalog import list_codex_models, list_models
from thinllm.chat_completions import ChatCompletionsClient
from thinllm.codex import CodexResponsesClient
from thinllm.errors import (
    InvalidLLMResponse,
    LLMAuthenticationError,
    LLMContextLengthError,
    LLMProviderError,
    LLMTransientError,
    MissingProviderSetting,
)
from thinllm.http_errors import describe_transport_error
from thinllm.providers import (
    PROVIDERS,
    Endpoint,
    resolve_endpoint,
)
from thinllm.responses import ResponsesClient
from thinllm.types import (
    ContentDeltaSink,
    LLMCallResult,
    LLMResponse,
    LLMUsage,
    ToolCall,
)

__all__ = [
    "PROVIDERS",
    "ChatCompletionsClient",
    "CodexResponsesClient",
    "ContentDeltaSink",
    "Endpoint",
    "InvalidLLMResponse",
    "LLMAuthenticationError",
    "LLMCallResult",
    "LLMContextLengthError",
    "LLMProviderError",
    "LLMResponse",
    "LLMTransientError",
    "LLMUsage",
    "MissingProviderSetting",
    "ResponsesClient",
    "ToolCall",
    "create_client",
    "describe_transport_error",
    "list_codex_models",
    "list_models",
    "resolve_endpoint",
]


def create_client(endpoint, **kwargs):
    """Select the built-in wire protocol without involving product code."""
    clients = {"openai": ResponsesClient, "openai-codex": CodexResponsesClient}
    return clients.get(endpoint.provider, ChatCompletionsClient)(endpoint, **kwargs)
