"""rest: the Bot declares it has nothing left to do and waits for something new to happen."""
from __future__ import annotations

from konden.runtime.dispatcher import ToolBinding

REST = "rest"

REST_SCHEMA = {"type": "function", "function": {
    "name": REST,
    "description": (
        "眼下没有能做的事了，休息。同一批里的其他调用照常执行，但结果不会再交给你，"
        "所以只把不需要再看结果的最后消息和它放在一起；还要看结果的调用先单独发出。"
        "后台任务、子智能体或别人的答复还没回来也可以休息，它们回来时会叫醒你；"
        "新消息、到点的定时提醒和澄清卡片的答复同样会叫醒你。只要还有现在就能做的事，就继续做，不要休息。"
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}}


async def _rest(context, arguments):
    return {"ok": True, "code": "RESTING", "data": None}


def rest_binding() -> dict[str, ToolBinding]:
    return {REST: ToolBinding(_rest, decision_on_outcome=False, ends_step=True)}
