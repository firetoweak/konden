"""Observable HTTP transport failures, with request credentials removed."""
from __future__ import annotations

import errno
import re
import socket
import ssl

import httpx


def _causes(error: BaseException):
    yield error
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            yield from _causes(child)
    # HTTP transports suppress the socket context when translating errors.
    cause = error.__cause__ or error.__context__
    if cause is not None:
        yield from _causes(cause)


def describe_transport_error(error: httpx.TransportError) -> str:
    """Retain the actual exception chain without headers, credentials or URL queries."""
    request = error.request
    url = request.url.copy_with(username=None, password=None, query=None, fragment=None)
    secrets = []
    for header in ("authorization", "proxy-authorization", "x-api-key", "cookie", "chatgpt-account-id"):
        value = request.headers.get(header)
        if value:
            secrets.extend((value, value.partition(" ")[2]))
    secrets.extend((request.url.username, request.url.password, *request.url.params.values()))

    def redact(value: str) -> str:
        value = value.replace(str(request.url), str(url))
        for secret in sorted(set(secrets) - {""}, key=len, reverse=True):
            value = value.replace(secret, "[已隐藏]").replace(repr(secret)[1:-1], "[已隐藏]")
        value = re.sub(r"(https?://)[^/\s@]+@", r"\1[已隐藏]@", value, flags=re.IGNORECASE)
        value = re.sub(r"(https?://[^\s?#'\"<>]+)[?#][^\s'\"<>]*", r"\1?[已隐藏]", value,
                       flags=re.IGNORECASE)
        value = re.sub(r"\b(Bearer|Basic)\s+[^\s'\"]+", r"\1 [已隐藏]", value, flags=re.IGNORECASE)
        return " ".join(value.split())

    causes = list(_causes(error))
    if any(isinstance(item, ssl.SSLCertVerificationError) for item in causes):
        summary = "HTTPS 证书校验失败。请检查系统信任证书或代理证书配置。"
    elif any(isinstance(item, ssl.SSLError) for item in causes):
        summary = "HTTPS 安全连接失败。请检查代理与 TLS 连接设置。"
    elif any(isinstance(item, socket.gaierror) for item in causes):
        summary = "域名解析失败（DNS）。请检查 DNS、网络或代理地址。"
    elif any(isinstance(item, ConnectionRefusedError)
             or isinstance(item, OSError) and item.errno in (errno.ECONNREFUSED, 10061) for item in causes):
        summary = "连接被拒绝。请确认服务或代理已启动，且地址、端口正确。"
    elif any(isinstance(item, OSError) and item.errno in (errno.ENETUNREACH, errno.EHOSTUNREACH) for item in causes):
        summary = "目标网络不可达。请检查网络或代理连接。"
    elif isinstance(error, httpx.TimeoutException):
        phase = {httpx.ConnectTimeout: "建立连接", httpx.ReadTimeout: "等待响应",
                 httpx.WriteTimeout: "发送请求", httpx.PoolTimeout: "等待可用连接"}.get(type(error), "连接")
        summary = f"{phase}时请求超时。请检查网络或代理。"
    elif isinstance(error, httpx.ProxyError):
        summary = "代理连接失败。请检查代理地址、认证和可用性。"
    elif isinstance(error, (httpx.LocalProtocolError, httpx.UnsupportedProtocol)):
        summary = "请求格式或地址协议错误。请检查连接设置中的密钥格式和服务地址。"
    elif isinstance(error, httpx.RemoteProtocolError):
        summary = "服务或代理返回了不完整或不符合 HTTP 协议的响应。请检查下方原因。"
    else:
        summary = "网络通信失败。请根据下方原因检查网络、代理或证书设置。"
    details = dict.fromkeys(f"{type(item).__name__}: {redact(str(item))}" for item in causes
                            if not isinstance(item, BaseExceptionGroup))
    return f"{summary}\n请求：{request.method} {url}\n底层原因：{' → '.join(details)}"
