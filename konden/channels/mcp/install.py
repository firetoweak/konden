"""Install the same-machine MCP connection through Codex's config API."""

from pathlib import Path
import sys

from konden.assistant.host.external_messages import ExternalConnectionNotFound

BRIDGE_MODULE = "konden.channels.mcp.bridge"
TOOL_TIMEOUT = 610
_ROOT = Path(__file__).resolve().parents[3]


class CodexInstallUnavailable(ValueError):
    pass


async def install_codex_mcp(service, address: str, external_id: str | None):
    server = await service.codex_link.reconnect()
    store = service.connections
    connection = store.create()[0] if external_id is None else store.get(external_id)
    if connection is None:
        raise ExternalConnectionNotFound(external_id)

    result = await server.request("config/value/write", {
        "keyPath": "mcp_servers.konden",
        "mergeStrategy": "replace",
        "value": {
            "command": str(Path(sys.executable).absolute()),
            "args": ["-m", BRIDGE_MODULE, address, str(store.path.resolve()), connection.connection_id],
            "env": {"PYTHONPATH": str(_ROOT)},
            "tool_timeout_sec": TOOL_TIMEOUT,
        },
    })
    if result["status"] == "okOverridden":
        raise CodexInstallUnavailable("Codex 的管理配置覆盖了 Konden 接入。请检查 Codex 的 MCP 设置后重试。")
    if result["status"] != "ok":
        raise ValueError("Unexpected Codex configuration write status")
    if store.get(connection.connection_id) is None:
        raise ExternalConnectionNotFound(connection.connection_id)
    return connection.view()
