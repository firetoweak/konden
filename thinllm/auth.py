"""OAuth credentials for the public Sign in with ChatGPT protocol."""
from __future__ import annotations

import asyncio
from base64 import urlsafe_b64encode
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from typing import Annotated
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4
import webbrowser

from filelock import FileLock
import httpx
import jwt
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from thinllm.errors import (
    ChatGPTReauthorizationRequired,
    InvalidLLMResponse,
    LLMAuthenticationError,
    LLMTransientError,
    MissingProviderSetting,
)

_ISSUER = "https://auth.openai.com"
_RESOURCE = "https://api.openai.com/v1"
_REGISTRATION_CLIENT = "dynamic_agent_client"
_PLAN_SCOPES = frozenset({"offline_access", "resource.invoke", "chatgpt.tokens.use.direct"})
_SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
_CALLBACK = "/auth/callback"
CHATGPT_SIGN_IN_TIMEOUT = 300
_REFRESH_REJECTED = frozenset({
    "invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
    "refresh_token_invalidated", "refresh_token_reused",
})
_Text = Annotated[str, Field(min_length=1)]


class _Tokens(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    access_token: _Text
    refresh_token: _Text | None = None
    id_token: _Text | None = None
    expires_in: int = Field(gt=0)
    scope: str | None = None


class _Grant(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    access_token: _Text
    refresh_token: _Text
    expires_at: int = Field(gt=0)
    scope: _Text


class _Account(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    client_id: _Text
    subject: _Text
    id_token: _Text
    grant: _Grant | None


class _Registration(BaseModel):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    host_id: _Text
    account: _Account | None = None


def chatgpt_auth_path(setting: str = "") -> Path:
    path = Path(setting.strip()).expanduser() if setting.strip() else Path.home() / ".thinllm" / "chatgpt.json"
    return path.resolve()


def _read_registration(path: Path) -> _Registration:
    try:
        return _Registration.model_validate_json(path.read_bytes())
    except FileNotFoundError as error:
        raise MissingProviderSetting("ChatGPT 登录") from error


def _save_registration(path: Path, registration: _Registration) -> None:
    # Replace the complete record so inference never reads a partial rotation.
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".chatgpt-")
    pending = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(registration.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        pending.replace(path)
    finally:
        pending.unlink(missing_ok=True)


def _account(registration: _Registration) -> _Account:
    account = registration.account
    if account is None or account.grant is None:
        raise MissingProviderSetting("ChatGPT 订阅授权")
    return account


def require_chatgpt_login(path: Path) -> None:
    _account(_read_registration(path))


def _token_request(form: dict[str, str]) -> _Tokens:
    try:
        response = httpx.post(f"{_ISSUER}/api/accounts/oauth/token", data={**form, "resource": _RESOURCE}, timeout=30)
    except httpx.TransportError as error:
        raise LLMTransientError("ChatGPT 授权服务连接失败，请稍后重试。") from error
    status = response.status_code
    if status == 200:
        try:
            return _Tokens.model_validate_json(response.content)
        except ValidationError:
            raise InvalidLLMResponse("invalid_oauth_response", "ChatGPT 返回了无效的 OAuth 凭据。") from None
    if status in {408, 425, 429, 500, 502, 503, 504}:
        raise LLMTransientError(f"ChatGPT 授权服务暂时不可用（HTTP {status}）。")
    if form["grant_type"] == "refresh_token" and status in {400, 401, 403}:
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = None
        error = body.get("error") if type(body) is dict else None
        code = error.get("code") if type(error) is dict else error
        if type(code) is str and code in _REFRESH_REJECTED:
            raise ChatGPTReauthorizationRequired("ChatGPT 授权已失效，请重新授权。")
    raise LLMAuthenticationError(f"ChatGPT 授权服务拒绝了请求（HTTP {status}）。请检查账号与授权。")


def _grant(tokens: _Tokens, scope: str) -> _Grant:
    if tokens.refresh_token is None:
        raise InvalidLLMResponse("invalid_oauth_response", "ChatGPT 授权响应缺少续期凭据。")
    return _Grant(access_token=tokens.access_token, refresh_token=tokens.refresh_token,
                  expires_at=int(time.time()) + tokens.expires_in, scope=scope)


def _access_token(path: Path) -> str:
    account = _account(_read_registration(path))
    if account.grant.expires_at > time.time() + 60:
        return account.grant.access_token
    with FileLock(str(path) + ".lock", timeout=30):
        registration = _read_registration(path)
        account = _account(registration)
        grant = account.grant
        if grant.expires_at > time.time() + 60:
            return grant.access_token
        try:
            tokens = _token_request({"grant_type": "refresh_token", "client_id": account.client_id,
                                     "refresh_token": grant.refresh_token})
            scope = grant.scope if tokens.scope is None else tokens.scope
            if not _PLAN_SCOPES.issubset(scope.split()):
                raise ChatGPTReauthorizationRequired("ChatGPT 订阅权限已失效，请重新授权。")
        except ChatGPTReauthorizationRequired:
            registration.account = account.model_copy(update={"grant": None})
            _save_registration(path, registration)
            raise
        account.grant = _grant(tokens, scope)
        _save_registration(path, registration)
        return account.grant.access_token


class ChatGPTAuth(httpx.Auth):
    def __init__(self, path: Path):
        self.path = path

    async def async_auth_flow(self, request):
        try:
            token = await asyncio.to_thread(_access_token, self.path)
        except MissingProviderSetting as error:
            raise ChatGPTReauthorizationRequired(error.setting) from error
        request.headers["Authorization"] = f"Bearer {token}"
        yield request


def _browser_callback(path: Path) -> tuple[ChatGPTSignIn, dict[str, list[str]]]:
    callback = None

    class Callback(BaseHTTPRequestHandler):
        timeout = 5

        def log_message(self, format, *args):
            # The callback URL contains the authorization code.
            pass

        def do_GET(self):
            nonlocal callback
            url = urlsplit(self.path)
            if url.path != _CALLBACK:
                self.send_error(404)
                return
            values = parse_qs(url.query, keep_blank_values=True)
            if not sign_in.accepts(values.get("state", [])):
                self.send_error(400)
                return
            callback = values
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write("已收到 ChatGPT 授权回调，请返回终端查看结果。".encode())

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        redirect = f"http://127.0.0.1:{server.server_port}{_CALLBACK}"
        sign_in = ChatGPTSignIn(path, redirect)
        if not webbrowser.open(sign_in.authorization_url):
            raise LLMAuthenticationError("无法打开系统浏览器，请在本机终端重新运行授权命令。")
        server.timeout = 1
        deadline = time.monotonic() + CHATGPT_SIGN_IN_TIMEOUT
        while callback is None and time.monotonic() < deadline:
            server.handle_request()
    if callback is None:
        raise LLMAuthenticationError("等待 ChatGPT 授权超时，请重新运行授权命令。")
    return sign_in, callback


def _verified_subject(id_token: str, client_id: str, nonce: str) -> str:
    try:
        key = jwt.PyJWKClient(f"{_ISSUER}/.well-known/jwks.json", timeout=10).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(id_token, key.key, algorithms=["RS256"], issuer=_ISSUER, audience=client_id,
                            options={"require": ["exp", "sub", "nonce"]})
    except jwt.PyJWKClientConnectionError as error:
        raise LLMTransientError("暂时无法验证 ChatGPT 签名，请稍后重新授权。") from error
    except (jwt.InvalidTokenError, jwt.PyJWKClientError):
        raise LLMAuthenticationError("ChatGPT 返回的身份无法验证，请重新授权。") from None
    if claims["nonce"] != nonce or not claims["sub"]:
        raise LLMAuthenticationError("ChatGPT 返回的身份与本次授权不匹配。")
    return claims["sub"]


class ChatGPTSignIn:
    """A single authorization attempt, independent of how its callback arrives."""

    def __init__(self, path: Path, redirect_uri: str):
        self._path = path
        self._redirect_uri = redirect_uri
        self._state = secrets.token_urlsafe(32)
        self._verifier = secrets.token_urlsafe(32)
        self._nonce = secrets.token_urlsafe(32)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with FileLock(str(path) + ".lock", timeout=30):
            try:
                registration = _read_registration(path)
            except MissingProviderSetting:
                registration = _Registration(host_id=f"urn:uuid:{uuid4()}")
                _save_registration(path, registration)
        self._registration = registration

    @property
    def authorization_url(self) -> str:
        previous = self._registration.account
        params = {
            "client_id": _REGISTRATION_CLIENT if previous is None else previous.client_id,
            "ext_agent_host_id": self._registration.host_id,
            "response_type": "code",
            "redirect_uri": self._redirect_uri,
            "scope": _SCOPES,
            "resource": _RESOURCE,
            "state": self._state,
            "nonce": self._nonce,
            "code_challenge": urlsafe_b64encode(sha256(self._verifier.encode()).digest()).decode().rstrip("="),
            "code_challenge_method": "S256",
        }
        if previous is None:
            params["agent_name_hint"] = "konden"
        else:
            params["id_token_hint"] = previous.id_token
            if previous.grant is None:
                params["prompt"] = "consent"
        return f"{_ISSUER}/api/accounts/authorize?{urlencode(params)}"

    def accepts(self, states: list[str]) -> bool:
        return len(states) == 1 and secrets.compare_digest(states[0].encode(), self._state.encode())

    def complete(self, callback: dict[str, list[str]]) -> None:
        if not self.accepts(callback.get("state", [])):
            raise LLMAuthenticationError("ChatGPT 回调与本次授权不匹配。")
        if "error" in callback:
            raise LLMAuthenticationError("ChatGPT 授权未完成。请确认同意使用订阅额度。")
        previous = self._registration.account
        codes = callback.get("code", [])
        client_ids = callback.get("client_id", [] if previous is None else [previous.client_id])
        if len(codes) != 1 or not codes[0] or len(client_ids) != 1 or client_ids[0] in {"", _REGISTRATION_CLIENT}:
            raise LLMAuthenticationError("ChatGPT 授权回调缺少有效的授权码或客户端标识。")
        client_id = client_ids[0]
        if previous is not None and client_id != previous.client_id:
            raise LLMAuthenticationError("ChatGPT 授权回调与当前账号注册不匹配。")
        tokens = _token_request({
            "grant_type": "authorization_code", "client_id": client_id, "code": codes[0],
            "code_verifier": self._verifier, "redirect_uri": self._redirect_uri,
        })
        if tokens.id_token is None:
            raise InvalidLLMResponse("invalid_oauth_response", "ChatGPT 授权响应缺少身份凭据。")
        subject = _verified_subject(tokens.id_token, client_id, self._nonce)
        if previous is not None and subject != previous.subject:
            raise LLMAuthenticationError("ChatGPT 授权账号与此前使用的账号不同。")
        scope = tokens.scope or ""
        grant = _grant(tokens, scope) if _PLAN_SCOPES.issubset(scope.split()) else None
        with FileLock(str(self._path) + ".lock", timeout=30):
            current = _read_registration(self._path)
            current_identity = None if current.account is None else current.account.id_token
            previous_identity = None if previous is None else previous.id_token
            if current.host_id != self._registration.host_id or current_identity != previous_identity:
                raise LLMAuthenticationError("已有另一次 ChatGPT 登录完成，请重新授权。")
            if grant is not None or current.account is None or current.account.grant is None:
                current.account = _Account(client_id=client_id, subject=subject, id_token=tokens.id_token, grant=grant)
                _save_registration(self._path, current)
        if grant is None:
            raise LLMAuthenticationError("当前账号尚未授权使用 ChatGPT 订阅额度，请重新授权并同意使用额度。")


def sign_in_chatgpt(path: Path) -> None:
    sign_in, callback = _browser_callback(path)
    sign_in.complete(callback)
