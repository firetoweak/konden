import { Alert, Button, Divider, Group, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";

import {
  useGetExternalConnectionsQuery, useInstallCodexMcpMutation,
  useRenameExternalConnectionMutation, useRevokeExternalConnectionMutation,
} from "../../api/kondenApi";
import { useAppSelector } from "../../app/hooks";

export function ExternalAccess() {
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const { currentData: connections = [], isLoading, error: loadError, refetch } = useGetExternalConnectionsQuery();
  const [installConnection, installing] = useInstallCodexMcpMutation();
  const [renameConnection, renaming] = useRenameExternalConnectionMutation();
  const [revokeConnection, revoking] = useRevokeExternalConnectionMutation();
  const [editing, setEditing] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);
  const [installed, setInstalled] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const busy = installing.isLoading || renaming.isLoading || revoking.isLoading;

  async function install(externalId?: string) {
    if (connectionId === null || busy) return;
    setError(null);
    setInstalled(null);
    try {
      const result = await installConnection({ connectionId, externalId }).unwrap();
      setInstalled(result.connection_id);
    } catch (cause) {
      setError(typeof cause === "string" ? cause : "安装失败，请确认本机 Codex 可用后重试。");
    }
  }

  async function rename(externalId: string) {
    if (connectionId === null || busy || !editName.trim()) return;
    setError(null);
    try {
      await renameConnection({ connectionId, externalId, name: editName.trim() }).unwrap();
      setEditing(null);
    } catch (cause) {
      setError(typeof cause === "string" ? cause : "重命名失败，请重试。");
    }
  }

  async function revoke(externalId: string) {
    if (connectionId === null || busy) return;
    setError(null);
    try {
      await revokeConnection({ connectionId, externalId }).unwrap();
      setConfirmRevoke(null);
      if (installed === externalId) setInstalled(null);
    } catch (cause) {
      setError(typeof cause === "string" ? cause : "撤销失败，请检查连接后重试。");
    }
  }

  return <Stack gap="md">
    <Text size="sm">在运行 Konden 的电脑上安装本机 Codex 接入。</Text>
    {installed === null ? null : <Alert color="green" role="status">已写入 Codex 配置。重新打开 Codex Desktop 后，在要接入的会话里发送「连上 Konden」。</Alert>}
    {error === null ? null : <Alert color="red" role="alert">{error}</Alert>}
    {connectionId === null ? <Text size="sm" c="dimmed">正在连接，恢复后可安装或撤销接入。</Text> : null}
    {loadError !== undefined ? <Alert color="orange" role="alert">
      无法读取接入。<Button size="compact-xs" variant="subtle" onClick={() => void refetch()}>重试</Button>
    </Alert> : isLoading ? <Text size="sm" c="dimmed">正在读取…</Text> : connections.length === 0 ? <Group>
      <Button loading={installing.isLoading} disabled={connectionId === null || busy} onClick={() => void install()}>安装到本机 Codex</Button>
    </Group> : <>
      <Divider />
      <Text size="sm">选择已有接入安装，可以继续使用之前关联的会话。</Text>
      {connections.map((item) => {
        const id = item.connection_id;
        return <Stack gap="xs" key={id}>
          <Group justify="space-between" align="flex-start">
            <Text fw={600} size="sm" style={{ minWidth: 0, overflowWrap: "anywhere" }}>{item.name}</Text>
            <Group gap="xs">
              <Button size="compact-xs" variant="light" disabled={connectionId === null || busy}
                aria-label={`安装到 Codex ${item.name}`} onClick={() => void install(id)}>安装到 Codex</Button>
              <Button size="compact-xs" variant="subtle" disabled={connectionId === null || busy}
                aria-label={`重命名 ${item.name}`} onClick={() => { setEditing(id); setEditName(item.name); }}>重命名</Button>
              <Button size="compact-xs" variant="subtle" color="red" disabled={connectionId === null || busy}
                aria-label={`撤销 ${item.name}`} onClick={() => setConfirmRevoke(id)}>撤销</Button>
            </Group>
          </Group>
          {editing !== id ? null : <>
            <TextInput label="修改接入名称" maxLength={80} value={editName} onChange={(event) => setEditName(event.currentTarget.value)}
              onKeyDown={(event) => { if (event.key === "Enter") void rename(id); }} />
            <Group gap="xs" justify="flex-end">
              <Button size="compact-xs" variant="subtle" disabled={busy} onClick={() => setEditing(null)}>取消命名</Button>
              <Button size="compact-xs" loading={renaming.isLoading} disabled={connectionId === null || busy || !editName.trim()}
                onClick={() => void rename(id)}>保存名称</Button>
            </Group>
          </>}
          {confirmRevoke !== id ? null : <>
            <Text size="sm" c="dimmed">撤销后，使用此配置的外部会话立即失去收发能力，已有对话记录保留。</Text>
            <Group gap="xs" justify="flex-end">
              <Button size="compact-xs" variant="subtle" disabled={busy} onClick={() => setConfirmRevoke(null)}>取消</Button>
              <Button size="compact-xs" color="red" loading={revoking.isLoading} disabled={connectionId === null || busy}
                onClick={() => void revoke(id)}>确认撤销</Button>
            </Group>
          </>}
        </Stack>;
      })}
    </>}
  </Stack>;
}
