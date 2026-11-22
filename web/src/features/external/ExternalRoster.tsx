import { ActionIcon, Alert, Button, Group, Loader, Menu, Modal, ScrollArea, Stack, Text, TextInput, Tooltip, UnstyledButton } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconArchive, IconDots, IconFolderPlus, IconPlugConnected, IconPlus, IconRefresh, IconSearchOff, IconTrash } from "@tabler/icons-react";
import { useState } from "react";
import { useMatch } from "react-router-dom";

import {
  useDeleteExternalSessionMutation, useGetCodexConnectionQuery,
  useGetExternalSessionsQuery, useGetNativeCodexChatsQuery,
  useImportLocalCodexMutation, useReconnectCodexMutation,
} from "../../api/kondenApi";
import type { ExternalSessionSummary } from "../../api/contracts";
import { useAppSelector } from "../../app/hooks";
import { sessionStatus } from "./sessionStatus";

export function ExternalRoster({ query, onOpen, compact = false }: { query: string; onOpen: (path: string) => void; compact?: boolean }) {
  const identity = useMatch("/external/:identity")?.params.identity;
  const { data: sessions = [], isLoading, error, refetch } = useGetExternalSessionsQuery();
  const [showRevoked, setShowRevoked] = useState(false);
  const [menuIdentity, setMenuIdentity] = useState<string | null>(null);
  const [removing, setRemoving] = useState<ExternalSessionSummary | null>(null);
  const [adding, setAdding] = useState(false);
  const revoked = sessions.filter((session) => !session.active && !session.local_codex);
  const visible = sessions.filter((session) => session.name.toLocaleLowerCase().includes(query));
  const entries = visible.filter((session) => session.active || session.local_codex || showRevoked || query !== "" || session.identity === identity);
  let notice = null;
  if (error !== undefined) {
    notice = compact
      ? <Tooltip label="无法读取外部会话，点击重试"><ActionIcon aria-label="重试读取外部会话" variant="subtle" color="red" onClick={() => void refetch()}><IconRefresh size={18} /></ActionIcon></Tooltip>
      : <Text c="dimmed" size="xs" px={10}>无法读取外部会话。<Button size="compact-xs" variant="subtle" onClick={() => void refetch()}>重试</Button></Text>;
  } else if (isLoading) {
    notice = compact ? <span className="sidebar-compact-notice" role="status" aria-label="正在读取外部会话"><Loader size={16} /></span>
      : <Text c="dimmed" size="xs" px={10}>正在读取外部会话…</Text>;
  } else if (query !== "" && visible.length === 0) {
    notice = compact ? <Tooltip label="没有匹配的外部会话"><span className="sidebar-compact-notice" role="status" aria-label="没有匹配的外部会话"><IconSearchOff size={18} /></span></Tooltip>
      : <Text c="dimmed" size="xs" px={10} py={8}>没有匹配的外部会话。</Text>;
  }
  function onAdded(identity: string) {
    setAdding(false);
    onOpen(`/external/${encodeURIComponent(identity)}`);
  }
  const actions = <>
    <CodexConnectionButton />
    <Tooltip label="添加已有 Codex 会话">
      <ActionIcon variant="subtle" color="gray" size="sm" aria-label="添加已有 Codex 会话" onClick={() => setAdding(true)}>
        <IconFolderPlus size={17} />
      </ActionIcon>
    </Tooltip>
    <Tooltip label="新建 Codex 会话">
      <ActionIcon variant="subtle" color="gray" size="sm" aria-label="新建 Codex 会话"
        aria-current={identity === "new" ? "page" : undefined} onClick={() => onOpen("/external/new")}>
        <IconPlus size={16} />
      </ActionIcon>
    </Tooltip>
  </>;
  return <section className="roster-section external-roster" aria-label="外部会话">
    {adding ? <ImportLocalCodexModal onClose={() => setAdding(false)} onAdded={onAdded} /> : null}
    {removing === null ? null : <RemoveExternalSessionModal target={removing} onClose={() => setRemoving(null)} onRemoved={() => {
      if (identity === removing.identity) onOpen("/");
      setRemoving(null);
    }} />}
    {compact ? <Stack align="center" gap={2}>{actions}</Stack> : <Group className="roster-section-header" justify="space-between" gap={0}>
      <Text fz={11}>外部会话</Text><Group gap={2}>{actions}</Group>
    </Group>}
    {notice}
    <Stack gap={3}>{entries.map((session) => <div key={session.identity}
      className={identity === session.identity ? "session-row is-active" : "session-row"}
      onContextMenu={(event) => { event.preventDefault(); setMenuIdentity(session.identity); }}>
      <UnstyledButton className="session-row-main" aria-label={session.name} title={session.name}
        aria-current={identity === session.identity ? "page" : undefined}
        onClick={() => onOpen(`/external/${encodeURIComponent(session.identity)}`)}>
        <Group gap={10} wrap="nowrap">
          <span className="agent-avatar agent-avatar-small" aria-hidden="true"><IconPlugConnected size={18} /></span>
          <Stack className="sidebar-agent-identity" gap={2}>
            <Text size="sm" truncate>{session.name}</Text>
            <Text c="dimmed" size="xs" truncate>
              {session.local_codex ? "Codex" : sessionStatus(session)}
            </Text>
          </Stack>
        </Group>
      </UnstyledButton>
      <Menu opened={menuIdentity === session.identity} onChange={(opened) => setMenuIdentity(opened ? session.identity : null)} position={compact ? "right-start" : "bottom-end"} withinPortal>
        <Menu.Target>{compact ? <span className="roster-menu-anchor" aria-hidden="true" /> : <ActionIcon className="roster-entry-menu" aria-label={`${session.name} 操作`} color="gray" variant="subtle" size="xs"><IconDots size={13} /></ActionIcon>}</Menu.Target>
        <Menu.Dropdown><Menu.Item color="red" leftSection={<IconTrash size={14} />} onClick={() => setRemoving(session)}>删除外部会话</Menu.Item></Menu.Dropdown>
      </Menu>
    </div>)}</Stack>
    {revoked.length === 0 || query !== "" ? null : <Button className="external-history-toggle" variant="subtle" color="gray" size="compact-xs"
      aria-label={showRevoked ? "收起已撤销会话" : `已撤销会话（${revoked.length}）`}
      title={showRevoked ? "收起已撤销会话" : `已撤销会话（${revoked.length}）`}
      aria-expanded={showRevoked} onClick={() => setShowRevoked(!showRevoked)}>
      {compact ? <IconArchive size={16} /> : showRevoked ? "收起已撤销会话" : `已撤销会话（${revoked.length}）`}
    </Button>}
  </section>;
}

function CodexConnectionButton() {
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const { data } = useGetCodexConnectionQuery();
  const [reconnect, { isLoading, error }] = useReconnectCodexMutation();
  const connecting = isLoading || data?.status === "connecting";
  const connected = data?.status === "connected";
  const detail = data?.error ?? (typeof error === "string" ? error : null);
  const label = connecting ? "正在连接 Codex" : connected ? "Codex 已连接" : detail ? "重新连接 Codex" : "连接 Codex";
  return <Tooltip label={detail && !connecting && !connected ? `${label} · ${detail}` : label}>
    <ActionIcon variant="subtle" color={connected ? "green" : detail ? "orange" : "gray"} size="sm"
      aria-label={label} aria-disabled={connectionId === null || connecting || connected} loading={connecting}
      onClick={() => { if (connectionId !== null && !connecting && !connected) void reconnect({ connectionId }); }}>
      {detail && !connected ? <IconRefresh size={16} /> : <IconPlugConnected size={16} />}
    </ActionIcon>
  </Tooltip>;
}

function ImportLocalCodexModal({ onClose, onAdded }: { onClose: () => void; onAdded: (identity: string) => void }) {
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const [query, setQuery] = useState("");
  const [search] = useDebouncedValue(query.trim(), 250);
  const [selected, setSelected] = useState<string | null>(null);
  const { currentData: chats = [], error: readError, isFetching, refetch } = useGetNativeCodexChatsQuery(search, { refetchOnMountOrArgChange: true });
  const [add, { isLoading, error: addError, reset }] = useImportLocalCodexMutation();
  const error = readError ?? addError;

  async function submit() {
    if (connectionId === null || selected === null || isLoading || isFetching) return;
    try {
      const { identity } = await add({ connectionId, identity: selected }).unwrap();
      onAdded(identity);
    } catch {
      // RTK Query exposes the error in this dialog.
    }
  }

  return <Modal opened title="添加已有 Codex 会话" onClose={onClose}
    closeOnClickOutside={!isLoading} closeOnEscape={!isLoading} withCloseButton={!isLoading}>
    <Stack gap="sm">
      <TextInput label="搜索会话" placeholder="按会话名称搜索" data-autofocus disabled={isLoading}
        value={query} onChange={(event) => { setQuery(event.currentTarget.value); setSelected(null); reset(); }} />
      {isFetching ? <Text c="dimmed" size="sm" role="status">正在读取 Codex 会话…</Text> : null}
      {error ? <Alert color="red" role="alert">
        {typeof error === "string" ? error : "无法读取或添加 Codex 会话。"}
        {readError ? <Button size="compact-xs" variant="subtle" color="red" onClick={() => void refetch()}>重试</Button> : null}
      </Alert> : null}
      {!isFetching && !readError && chats.length === 0 ? <Text c="dimmed" size="sm">没有找到 Codex 会话。</Text> : null}
      <ScrollArea.Autosize mah={300} offsetScrollbars>
        <Stack gap={3}>
          {chats.map((chat) => <UnstyledButton key={chat.identity} aria-pressed={chat.identity === selected}
            disabled={isLoading || search !== query.trim()} onClick={() => { setSelected(chat.identity); reset(); }}
            className={chat.identity === selected ? "session-row is-active" : "session-row"}>
            <Text size="sm" lineClamp={2}>{chat.name}</Text>
            <Text size="xs" c="dimmed" truncate title={chat.workspace ?? undefined}>
              {chat.identity.slice(-8)}{chat.workspace ? ` · ${chat.workspace}` : ""}
            </Text>
          </UnstyledButton>)}
        </Stack>
      </ScrollArea.Autosize>
      <Group justify="flex-end">
        <Button variant="default" disabled={isLoading} onClick={onClose}>取消</Button>
        <Button disabled={connectionId === null || selected === null || isFetching || Boolean(readError)}
          loading={isLoading} onClick={() => void submit()}>添加会话</Button>
      </Group>
    </Stack>
  </Modal>;
}

function RemoveExternalSessionModal({ target, onClose, onRemoved }: {
  target: ExternalSessionSummary; onClose: () => void; onRemoved: () => void;
}) {
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const [remove, { isLoading }] = useDeleteExternalSessionMutation();
  const [error, setError] = useState<string | null>(null);
  async function confirm() {
    if (connectionId === null || isLoading) return;
    setError(null);
    try {
      await remove({ connectionId, identity: target.identity }).unwrap();
      onRemoved();
    } catch (cause) {
      setError(typeof cause === "string" ? cause : "删除失败，请重试。");
    }
  }
  return <Modal opened title={`删除外部会话「${target.name}」？`} onClose={onClose}
    closeOnClickOutside={!isLoading} closeOnEscape={!isLoading} withCloseButton={!isLoading}>
    <Stack gap="md">
      {target.local_codex ? <Text size="sm">从 konden 移除此会话。会话和聊天记录可以继续在 Codex Desktop 查看。</Text>
        : <>
          <Text size="sm">删除 konden 中的接入记录，停止收发和自动接续。Bot 对话中的已有往来保留。</Text>
          <Text size="sm" c="dimmed">原生聊天以后主动重新接入时，会话会重新出现在侧栏。</Text>
        </>}
      {error === null ? null : <Alert color="red" role="alert">{error}</Alert>}
      <Group justify="flex-end">
        <Button data-autofocus variant="default" disabled={isLoading} onClick={onClose}>取消</Button>
        <Button color="red" loading={isLoading} disabled={connectionId === null} onClick={() => void confirm()}>删除外部会话</Button>
      </Group>
    </Stack>
  </Modal>;
}
