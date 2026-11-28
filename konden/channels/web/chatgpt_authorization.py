"""The local Web entry point for a ChatGPT subscription sign-in."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import secrets
import time

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import PlainTextResponse, RedirectResponse

from thinllm.auth import CHATGPT_SIGN_IN_TIMEOUT, ChatGPTSignIn
from thinllm.errors import InvalidLLMResponse, LLMAuthenticationError, LLMTransientError

CALLBACK_PATH = "/auth/callback"
_PRIVATE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
_LOCAL_ONLY = "请在运行 konden 的电脑上打开模型配置完成 ChatGPT 授权。"
_EXPIRED = "等待 ChatGPT 授权超时，请重试。"


def require_local_browser(request: Request) -> int:
    if (request.client is None or request.client.host not in {"127.0.0.1", "::1"}
            or request.url.scheme != "http" or request.url.hostname not in {"127.0.0.1", "localhost"}):
        raise HTTPException(403, _LOCAL_ONLY)
    if request.method == "POST" and request.headers.get("origin") != f"http://{request.url.netloc}":
        raise HTTPException(403, _LOCAL_ONLY)
    return request.scope["server"][1]


@dataclass(slots=True)
class _Pending:
    id: str
    sign_in: ChatGPTSignIn
    deadline: float
    exchanging: bool = False


@dataclass(frozen=True, slots=True)
class _Result:
    id: str
    message: str | None = None


class ChatGPTAuthorization:
    """One pending authorization for this Web instance. Only the grant is durable."""

    def __init__(self) -> None:
        self._starting = False
        self._attempt: _Pending | _Result | None = None

    def _expire(self) -> None:
        attempt = self._attempt
        if isinstance(attempt, _Pending) and not attempt.exchanging and time.monotonic() >= attempt.deadline:
            self._attempt = _Result(attempt.id, _EXPIRED)

    async def start(self, begin: Callable[[str], ChatGPTSignIn], port: int) -> dict[str, str]:
        self._expire()
        if isinstance(self._attempt, _Pending) and not self._attempt.exchanging:
            return {"authorization_id": self._attempt.id}
        if self._starting or isinstance(self._attempt, _Pending):
            raise HTTPException(409, "已有一次 ChatGPT 授权正在进行，请先完成该次授权。")
        self._starting = True
        try:
            sign_in = await run_in_threadpool(begin, f"http://127.0.0.1:{port}{CALLBACK_PATH}")
            attempt = _Pending(secrets.token_urlsafe(24), sign_in, time.monotonic() + CHATGPT_SIGN_IN_TIMEOUT)
            self._attempt = attempt
        finally:
            self._starting = False
        return {"authorization_id": attempt.id}

    def open(self, authorization_id: str) -> RedirectResponse:
        self._expire()
        attempt = self._attempt
        if not isinstance(attempt, _Pending) or attempt.id != authorization_id or attempt.exchanging:
            raise HTTPException(404, "找不到正在等待的 ChatGPT 授权。请返回 konden 查看连接。")
        return RedirectResponse(attempt.sign_in.authorization_url, status_code=303, headers=_PRIVATE)

    def status(self, authorization_id: str) -> dict[str, str]:
        self._expire()
        attempt = self._attempt
        if attempt is None or attempt.id != authorization_id:
            raise HTTPException(404, "找不到本次授权。请确认连接状态后重试。")
        if isinstance(attempt, _Pending):
            return {"state": "waiting"}
        if attempt.message is not None:
            return {"state": "failed", "message": attempt.message}
        return {"state": "connected"}

    async def callback(self, request: Request) -> PlainTextResponse:
        self._expire()
        attempt = self._attempt
        values = {key: request.query_params.getlist(key) for key in request.query_params}
        if (not isinstance(attempt, _Pending) or attempt.exchanging
                or not attempt.sign_in.accepts(values.get("state", []))):
            return PlainTextResponse("找不到匹配的 ChatGPT 授权。请返回 konden 查看连接。",
                                     status_code=400, headers=_PRIVATE)
        attempt.exchanging = True
        try:
            await run_in_threadpool(attempt.sign_in.complete, values)
        except (LLMAuthenticationError, LLMTransientError, InvalidLLMResponse) as error:
            result = _Result(attempt.id, str(error))
            message = f"{error} 请返回 konden 查看连接。"
        else:
            result = _Result(attempt.id)
            message = "ChatGPT 授权完成，可以返回 konden 选择模型。"
        finally:
            self._attempt = None
        self._attempt = result
        return PlainTextResponse(message, headers=_PRIVATE)
