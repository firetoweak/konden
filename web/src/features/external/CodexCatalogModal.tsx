import { ActionIcon, Alert, Button, Collapse, Group, Modal, ScrollArea, Stack, Text, TextInput, UnstyledButton } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { IconChevronDown, IconChevronRight, IconFolder, IconPlus } from "@tabler/icons-react";
import { useId, useState } from "react";

import type { NativeCodexCatalog } from "../../api/contracts";
import { useGetNativeCodexChatsQuery, useImportLocalCodexMutation } from "../../api/kondenApi";
import { useAppSelector } from "../../app/hooks";

export function CodexCatalogModal({ onClose, onAdded, onNew }: {
  onClose: () => void; onAdded: (identity: string) => void; onNew: (projectId: string) => void;
}) {
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const [query, setQuery] = useState("");
  const [search] = useDebouncedValue(query.trim(), 250);
  const [selected, setSelected] = useState<string | null>(null);
  const { currentData: catalog, error: readError, isFetching, refetch } = useGetNativeCodexChatsQuery(search, { refetchOnMountOrArgChange: true });
  const [add, { isLoading, error: addError, reset }] = useImportLocalCodexMutation();
  const error = readError ?? addError;
  const changingSearch = search !== query.trim();

  async function submit() {
    if (connectionId === null || selected === null || isLoading || isFetching || changingSearch || readError) return;
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
      {catalog?.projects_available === false ? <Text c="dimmed" size="sm">当前 Codex 版本未提供工作区目录。</Text> : null}
      {!isFetching && !readError && catalog?.groups.length === 0 ? <Text c="dimmed" size="sm">没有找到 Codex 会话。</Text> : null}
      <ScrollArea.Autosize mah="min(53vh, 430px)" scrollbars="y" offsetScrollbars styles={{ content: { width: "100%", minWidth: 0 } }}>
        <Stack gap={7}>
          {catalog?.groups.map((group) => <CatalogGroup key={group.project ? `project:${group.project.id}` : "other"}
            group={group} selected={selected} searching={search !== ""} disabled={isLoading || changingSearch || isFetching}
            onSelect={(identity) => { setSelected(identity); reset(); }} onNew={onNew} />)}
        </Stack>
      </ScrollArea.Autosize>
      <Group justify="flex-end">
        <Button variant="default" disabled={isLoading} onClick={onClose}>取消</Button>
        <Button disabled={connectionId === null || selected === null || isFetching || changingSearch || Boolean(readError)}
          loading={isLoading} onClick={() => void submit()}>添加会话</Button>
      </Group>
    </Stack>
  </Modal>;
}

function CatalogGroup({ group, selected, searching, disabled, onSelect, onNew }: {
  group: NativeCodexCatalog["groups"][number]; selected: string | null; searching: boolean; disabled: boolean;
  onSelect: (identity: string) => void; onNew: (projectId: string) => void;
}) {
  const panelId = useId();
  const [collapsed, setCollapsed] = useState(false);
  const { project, chats } = group;
  const name = project?.name ?? "其他会话";
  const expanded = chats.length > 0 && (searching || !collapsed);
  const groupTitle = project?.roots.map((root) => root.path).join("\n") || name;

  return <section aria-label={name}>
    <Group className="roster-section-header" gap={2} wrap="nowrap">
      <UnstyledButton className="roster-section-toggle" aria-expanded={expanded} aria-controls={panelId}
        disabled={chats.length === 0 || searching} title={groupTitle} onClick={() => setCollapsed((value) => !value)}>
        {expanded ? <IconChevronDown size={12} /> : <IconChevronRight size={12} />}
        <IconFolder size={15} />
        <Text size="sm" fw={500} truncate>{name}</Text>
      </UnstyledButton>
      {project?.roots.length ? <ActionIcon variant="subtle" color="gray" size="sm" disabled={disabled}
        aria-label={`在 ${name} 新建 Codex 会话`} title="新建 Codex 会话" onClick={() => onNew(project.id)}>
        <IconPlus size={16} />
      </ActionIcon> : null}
    </Group>
    <Collapse id={panelId} expanded={expanded}>
      <Stack gap={2} ps={17}>
        {chats.map((chat) => <UnstyledButton key={chat.identity} aria-pressed={chat.identity === selected}
          disabled={disabled} onClick={() => onSelect(chat.identity)}
          className={chat.identity === selected ? "session-row is-active" : "session-row"}>
          <Text size="sm" lineClamp={2}>{chat.name}</Text>
          <Text size="xs" c="dimmed" truncate title={chat.workspace ?? undefined}>
            {chat.identity.slice(-8)}{chat.workspace ? ` · ${chat.workspace}` : ""}
          </Text>
        </UnstyledButton>)}
      </Stack>
    </Collapse>
  </section>;
}
