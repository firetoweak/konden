from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter

from konden.config import write_json
from konden.file_lock import exclusive
from konden.paths import KondenHome
from konden.mcp.models import (
    McpServerRecord,
    McpServerRuntimeState,
    RuntimeAvailability,
    utc_now,
)


class _BundledCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(strict=True, gt=0)
    status: RuntimeAvailability
    checked_at: AwareDatetime
    error_summary: str | None


_bundled_checks = TypeAdapter(dict[str, _BundledCheck])


class McpRegistry:
    """用户安装配置与 MCP 检测结果的持久读写。"""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._path = self._root / "servers.json"
        # 数据根由所有 Agent 进程共享；读写都在这把锁内，写回基于锁内读到的最新内容。
        self._lock_path = self._path.with_suffix(".lock")

    @classmethod
    def from_home(cls, home: KondenHome) -> "McpRegistry":
        return cls(home.mcp_root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def path(self) -> Path:
        return self._path

    async def list_servers(self) -> tuple[McpServerRecord, ...]:
        with exclusive(self._lock_path):
            return self._read_unlocked()

    def snapshot(self) -> tuple[McpServerRecord, ...]:
        """同步读取一次文件快照，供 Toolset 目录投影。"""
        with exclusive(self._lock_path):
            return self._read_unlocked()

    def bundled_with_checks(self, records: tuple[McpServerRecord, ...]) -> tuple[McpServerRecord, ...]:
        with exclusive(self._lock_path):
            checks = self._read_bundled_checks()
        return tuple(
            replace(record, last_status=check.status, last_checked_at=check.checked_at,
                    last_error_summary=check.error_summary or "")
            if (check := checks.get(record.id)) is not None and check.revision == record.revision
            else record
            for record in records
        )

    async def mark_bundled_tested(self, record: McpServerRecord, runtime: McpServerRuntimeState) -> None:
        # Only the observation is stored; product configuration remains owned by the bundled provider.
        check = _BundledCheck(revision=record.revision, status=runtime.status,
                              checked_at=runtime.last_checked_at, error_summary=runtime.last_error_summary)
        with exclusive(self._lock_path):
            checks = self._read_bundled_checks()
            checks[record.id] = check
            write_json(self._root / "builtin-checks.json", _bundled_checks.dump_python(checks, mode="json"))

    def _read_bundled_checks(self) -> dict[str, _BundledCheck]:
        path = self._root / "builtin-checks.json"
        if not path.exists():
            return {}
        return _bundled_checks.validate_json(path.read_text(encoding="utf-8"))

    async def get(self, server_id: str) -> McpServerRecord | None:
        with exclusive(self._lock_path):
            return self._index_unlocked().get(server_id)

    async def replace_all(
        self,
        records: Mapping[str, McpServerRecord],
    ) -> tuple[McpServerRecord, ...]:
        with exclusive(self._lock_path):
            ordered = tuple(
                sorted(records.values(), key=lambda item: item.id)
            )
            self._write_unlocked(ordered)
            return ordered

    async def upsert(self, record: McpServerRecord) -> McpServerRecord:
        with exclusive(self._lock_path):
            index = self._index_unlocked()
            existing = index.get(record.id)
            if existing is None:
                stored = record
            else:
                stored = McpServerRecord(
                    id=record.id,
                    display_name=record.display_name,
                    description=record.description,
                    transport=record.transport,
                    transport_config=record.transport_config,
                    enabled=record.enabled,
                    read_only=record.read_only,
                    revision=existing.revision + 1,
                    created_at=existing.created_at,
                    updated_at=utc_now(),
                )
            index[record.id] = stored
            self._write_unlocked(
                tuple(sorted(index.values(), key=lambda item: item.id))
            )
            return stored

    async def set_enabled(
        self,
        server_id: str,
        enabled: bool,
    ) -> McpServerRecord:
        with exclusive(self._lock_path):
            index = self._index_unlocked()
            existing = index.get(server_id)
            if existing is None:
                raise KeyError(server_id)
            if existing.enabled == enabled:
                return existing
            updated = McpServerRecord(
                id=existing.id,
                display_name=existing.display_name,
                description=existing.description,
                transport=existing.transport,
                transport_config=existing.transport_config,
                enabled=enabled,
                read_only=existing.read_only,
                revision=existing.revision + 1,
                created_at=existing.created_at,
                updated_at=utc_now(),
                last_status=existing.last_status,
                last_checked_at=existing.last_checked_at,
                last_error_summary=existing.last_error_summary,
            )
            index[server_id] = updated
            self._write_unlocked(
                tuple(sorted(index.values(), key=lambda item: item.id))
            )
            return updated

    async def mark_tested(
        self,
        server_id: str,
        status: RuntimeAvailability,
        *,
        error_summary: str = "",
    ) -> McpServerRecord:
        """持久化最近一次测试结果，不改变配置 revision。"""
        with exclusive(self._lock_path):
            index = self._index_unlocked()
            existing = index.get(server_id)
            if existing is None:
                raise KeyError(server_id)
            updated = McpServerRecord(
                id=existing.id,
                display_name=existing.display_name,
                description=existing.description,
                transport=existing.transport,
                transport_config=existing.transport_config,
                enabled=existing.enabled,
                read_only=existing.read_only,
                revision=existing.revision,
                created_at=existing.created_at,
                updated_at=utc_now(),
                last_status=status,
                last_checked_at=utc_now(),
                last_error_summary=error_summary,
            )
            index[server_id] = updated
            self._write_unlocked(
                tuple(sorted(index.values(), key=lambda item: item.id))
            )
            return updated

    async def remove(self, server_id: str) -> McpServerRecord:
        with exclusive(self._lock_path):
            index = self._index_unlocked()
            existing = index.pop(server_id, None)
            if existing is None:
                raise KeyError(server_id)
            self._write_unlocked(
                tuple(sorted(index.values(), key=lambda item: item.id))
            )
            return existing

    def _index_unlocked(self) -> dict[str, McpServerRecord]:
        return {record.id: record for record in self._read_unlocked()}

    def _read_unlocked(self) -> tuple[McpServerRecord, ...]:
        if not self._path.exists():
            return ()
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"version", "servers"}
            or type(payload["version"]) is not int
            or payload["version"] != 1
        ):
            raise ValueError("servers.json envelope 格式无效")
        servers = payload["servers"]
        if not isinstance(servers, list):
            raise ValueError("servers.json 格式无效")
        if any(not isinstance(item, dict) for item in servers):
            raise ValueError("servers.json server 必须是 object")
        records = tuple(McpServerRecord.from_dict(item) for item in servers)
        ids = [record.id for record in records]
        if len(ids) != len(set(ids)):
            raise ValueError("servers.json 包含重复 id")
        return records

    def _write_unlocked(self, records: tuple[McpServerRecord, ...]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "servers": [record.to_dict() for record in records],
        }
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self._path)
