"""ChatGPT plan usage over the public Responses endpoint."""
from thinllm.auth import ChatGPTAuth
from thinllm.errors import LLMAuthenticationError, LLMProviderError, LLMTransientError
from thinllm.responses import ResponsesClient


class ChatGPTResponsesClient(ResponsesClient):
    def __init__(self, endpoint, **kwargs):
        super().__init__(endpoint, auth=ChatGPTAuth(endpoint.auth_file), **kwargs)

    def _payload(self, messages, model, tools, reasoning_effort):
        payload = super()._payload(messages, model, tools, reasoning_effort)
        if tools:
            payload["input"].append({"type": "additional_tools", "role": "developer", "tools": payload.pop("tools")})
        return payload

    def _raise_llm_error(self, status_code, code, message):
        if code == "subscription_sharing_usage_limit_exceeded":
            raise LLMProviderError("ChatGPT 订阅用量已达上限，请查看 https://chatgpt.com/settings/usage 。")
        if code in {"subscription_sharing_usage_unavailable", "subscription_sharing_user_unavailable"}:
            raise LLMTransientError("暂时无法读取 ChatGPT 订阅用量或账号，请稍后重试。")
        if code == "subscription_sharing_user_not_eligible":
            raise LLMProviderError("当前 ChatGPT 账号或工作区无法使用订阅额度，请检查账号与工作区权限。")
        if code == "subscription_sharing_invalid_user":
            raise LLMAuthenticationError("ChatGPT 授权已失效，请运行 python -m thinllm auth chatgpt 重新授权。")
        super()._raise_llm_error(status_code, code, message)
