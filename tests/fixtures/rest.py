from konden.assistant.rest import REST
from konden.llm.api import LLMResponse, ToolCall


def offers_rest(tools) -> bool:
    return any(tool["function"]["name"] == REST for tool in tools or ())


def finish(tools, content: str = "done") -> LLMResponse:
    """End the turn the way the session allows: a Bot rests, other sessions answer in text."""
    if offers_rest(tools):
        return LLMResponse(calls=(ToolCall("rest", REST, "{}"),))
    return LLMResponse(content=content)
