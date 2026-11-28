import { ActionIcon, AppShell, Group, Popover, ScrollArea, Stack, Text, TextInput, Tooltip } from "@mantine/core";
import { IconMessages, IconPlugConnected, IconPlugConnectedX, IconPlus, IconSearch, IconX } from "@tabler/icons-react";
import { useEffect, useRef, useState } from "react";
import { useMatch, useNavigate } from "react-router-dom";
import { useGetAgentsQuery, useGetGroupsQuery, useGetInboxQuery } from "../../api/kondenApi";
import { useAppSelector } from "../../app/hooks";
import { ColorSchemeSwitcher } from "../../app/ColorSchemeSwitcher";
import { BotRoster } from "./BotRoster";
import { SettingsMenu, type SettingsSection } from "../settings/SettingsMenu";
import { ExternalRoster } from "../external/ExternalRoster";
import { CapabilitiesIcon } from "../capabilities/CapabilitiesIcon";

export function AgentSidebar({ onNavigate, onCreateAgent, onOpenSettings, compact = false }: {
  onNavigate: () => void;
  onCreateAgent: () => void;
  onOpenSettings: (section: SettingsSection) => void;
  compact?: boolean;
}) {
  const navigate = useNavigate();
  const sessionId = useMatch("/sessions/:sessionId")?.params.sessionId;
  const groupId = useMatch("/groups/:groupId")?.params.groupId;
  const capabilitiesOpened = useMatch("/capabilities") !== null;
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const { data: agents = [] } = useGetAgentsQuery();
  const { data: chats = [] } = useGetGroupsQuery();
  const { data: inbox = [] } = useGetInboxQuery();
  const [search, setSearch] = useState("");
  const [searchOpened, setSearchOpened] = useState(false);
  const searchInput = useRef<HTMLInputElement>(null);
  const searchTrigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const focusSearch = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault(); setSearchOpened(true); searchInput.current?.focus();
      }
    };
    window.addEventListener("keydown", focusSearch);
    return () => window.removeEventListener("keydown", focusSearch);
  }, []);
  const query = search.trim().toLocaleLowerCase();
  const open = (path: string) => { navigate(path); onNavigate(); };
  const closeSearch = () => { setSearch(""); setSearchOpened(false); searchTrigger.current?.focus(); };
  const searchField = <TextInput autoFocus ref={searchInput} className="sidebar-search" aria-label="搜索对话" placeholder="搜索对话" value={search} mt={compact ? 0 : 12} size="sm" leftSection={<IconSearch size={14} />} rightSection={search === "" ? <kbd>Ctrl K</kbd> : <ActionIcon aria-label="清除搜索" size="xs" onClick={() => { setSearch(""); searchInput.current?.focus(); }}><IconX size={12} /></ActionIcon>} rightSectionWidth={48} onChange={(event) => setSearch(event.currentTarget.value)} onKeyDown={(event) => { if (event.key === "Escape") closeSearch(); }} />;
  return <Stack h="100%" gap="md" className="sidebar-content">
    <AppShell.Section>
      <Group className="sidebar-toolbar" justify="space-between" gap={8}>
        <Tooltip label="所有对话" events={{ hover: true, focus: true, touch: false }}>
          <ActionIcon aria-label="所有对话" variant="subtle" color="gray" size={32} onClick={() => open("/")}><IconMessages size={19} /></ActionIcon>
        </Tooltip>
        <Group className="sidebar-actions" gap={4}>
          <Tooltip label="新建 Bot" events={{ hover: true, focus: true, touch: false }}>
            <ActionIcon className="sidebar-create" aria-label="新建 Bot" variant="subtle" size={32} disabled={connectionId === null} onClick={onCreateAgent}><IconPlus size={19} /></ActionIcon>
          </Tooltip>
          <Popover opened={compact && searchOpened} onClose={closeSearch} position="right-start" width={260} withinPortal>
            <Popover.Target>
              <ActionIcon ref={searchTrigger} aria-label="搜索对话" title="搜索对话（Ctrl+K）" aria-expanded={searchOpened} variant="subtle" color="gray" size={32} onClick={() => searchOpened ? closeSearch() : setSearchOpened(true)}><IconSearch size={19} /></ActionIcon>
            </Popover.Target>
            <Popover.Dropdown>{compact ? searchField : null}</Popover.Dropdown>
          </Popover>
        </Group>
      </Group>
      {searchOpened && !compact ? searchField : null}
    </AppShell.Section>
    <AppShell.Section grow className="session-section">
      <ScrollArea className="session-scroll" type="hover"><BotRoster compact={compact} agents={agents.filter((agent) => agent.name.toLocaleLowerCase().includes(query))} chats={chats.filter((chat) => chat.name.toLocaleLowerCase().includes(query))} attentionSessionIds={new Set(inbox.map((item) => item.session_id))} sessionId={sessionId} groupId={groupId} searching={query !== ""} onOpen={open} />
        <ExternalRoster compact={compact} query={query} onOpen={open} />
      </ScrollArea>
    </AppShell.Section>
    <AppShell.Section className="sidebar-footer"><Group className="sidebar-footer-actions" gap={4} wrap="wrap">
      <Tooltip label="能力" events={{ hover: true, focus: true, touch: false }}>
        <ActionIcon className="capabilities-trigger" aria-label="能力" aria-current={capabilitiesOpened ? "page" : undefined}
          variant="subtle" color="gray" size={32} onClick={() => open("/capabilities")}><CapabilitiesIcon size={20} /></ActionIcon>
      </Tooltip>
      <SettingsMenu onOpen={onOpenSettings} /><ColorSchemeSwitcher />
      <Tooltip label={connectionId === null ? "正在连接…" : "已连接"}>
        <span className="connection-status" data-connected={connectionId !== null} role="status" aria-label={connectionId === null ? "正在连接…" : "已连接"}>
          {connectionId === null ? <IconPlugConnectedX aria-hidden="true" size={16} /> : <IconPlugConnected aria-hidden="true" size={16} />}
        </span>
      </Tooltip>
    </Group>{connectionId === null && !compact ? <Text size="xs" c="dimmed" px={8}>正在连接…</Text> : null}</AppShell.Section>
  </Stack>;
}
