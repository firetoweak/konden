import { ActionIcon, AppShell } from "@mantine/core";
import { useDisclosure, useMediaQuery } from "@mantine/hooks";
import { IconMenu2 } from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Route, Routes, useNavigate } from "react-router-dom";

import { useAppDispatch, useAppSelector } from "./hooks";
import { useGetAgentsQuery, useGetAvatarsQuery } from "../api/kondenApi";
import { BotAvatars, BotIdentities } from "../features/conversation/ChatPresentation";
import {
  MAX_SIDEBAR_WIDTH,
  MIN_SIDEBAR_WIDTH,
  COMPACT_SIDEBAR_THRESHOLD,
  clampSidebarWidth,
  readSidebarWidth,
  writeSidebarWidth,
} from "./sidebarWidth";
import { SessionConversation } from "../features/conversation/SessionConversation";
import { GroupChatPage } from "../features/groups/GroupChatPage";
import { AgentHome } from "../features/sessions/AgentHome";
import { AgentSidebar } from "../features/sessions/AgentSidebar";
import { CreateAgentModal } from "../features/sessions/CreateAgentModal";
import { SettingsDrawer, type SettingsSection } from "../features/settings/SettingsMenu";
import { ExternalSessionPage } from "../features/external/ExternalSessionPage";
import { NewLocalCodexConversation } from "../features/external/LocalCodexConversation";
import { CapabilitiesPage } from "../features/capabilities/CapabilitiesPage";
import { openEventBridge } from "../realtime/eventBridge";

type Dialog = { kind: "create"; returnedFromSettings?: true }
  | { kind: "settings" }
  | { kind: "create-settings" }
  | null;

export function App() {
  const navigate = useNavigate();
  const dispatch = useAppDispatch();
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const { data: agents } = useGetAgentsQuery();
  const { data: avatars = {} } = useGetAvatarsQuery();
  const identities = useMemo(() => new Map(agents?.map((agent) => [agent.name, agent.session_id])), [agents]);
  const [navigationOpened, { close: closeNavigation, toggle: toggleNavigation }] =
    useDisclosure(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>("models");
  const [sidebarWidth, setSidebarWidth] = useState(readSidebarWidth);
  const compactSidebar = useMediaQuery("(min-width: 48em)") && sidebarWidth < COMPACT_SIDEBAR_THRESHOLD;
  const [resizingSidebar, setResizingSidebar] = useState(false);
  const sidebarWidthRef = useRef(sidebarWidth);
  const resizingSidebarRef = useRef(false);
  sidebarWidthRef.current = sidebarWidth;

  useEffect(() => openEventBridge(dispatch), [dispatch]);

  function openCreateAgent() {
    setDialog({ kind: "create" });
  }

  function openSettings(section: SettingsSection) {
    setSettingsSection(section);
    setDialog({ kind: "settings" });
    closeNavigation();
  }

  function openCreateSettings() {
    setSettingsSection("models");
    setDialog({ kind: "create-settings" });
  }

  function closeSettings() {
    setDialog((current) => {
      if (current !== dialog) return current;
      return current?.kind === "create-settings" ? { kind: "create", returnedFromSettings: true } : null;
    });
  }

  function beginSidebarResize(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) {
      return;
    }
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // 没有真实 pointer 时仍按拖动手势处理
    }
    resizingSidebarRef.current = true;
    setResizingSidebar(true);
    document.body.classList.add("is-resizing-sidebar");
  }

  function moveSidebarResize(event: PointerEvent<HTMLDivElement>) {
    if (!resizingSidebarRef.current) {
      return;
    }
    const next = clampSidebarWidth(event.clientX);
    sidebarWidthRef.current = next;
    setSidebarWidth(next);
  }

  function endSidebarResize() {
    if (!resizingSidebarRef.current) {
      return;
    }
    resizingSidebarRef.current = false;
    setResizingSidebar(false);
    document.body.classList.remove("is-resizing-sidebar");
    writeSidebarWidth(sidebarWidthRef.current);
  }

  function resizeSidebarWithKeyboard(event: KeyboardEvent<HTMLDivElement>) {
    const width = event.key === "Home" ? MIN_SIDEBAR_WIDTH : event.key === "End" ? MAX_SIDEBAR_WIDTH
      : event.key === "ArrowLeft" ? sidebarWidth - 16 : event.key === "ArrowRight" ? sidebarWidth + 16 : null;
    if (width === null) return;
    event.preventDefault();
    const next = clampSidebarWidth(width);
    sidebarWidthRef.current = next;
    setSidebarWidth(next);
    writeSidebarWidth(next);
  }

  return (
    <BotIdentities value={identities}>
    <BotAvatars value={avatars}>
    <AppShell
      className="app-shell"
      navbar={{
        width: sidebarWidth,
        breakpoint: "sm",
        collapsed: { mobile: !navigationOpened },
      }}
      padding={0}
      transitionDuration={resizingSidebar ? 0 : 180}
    >
      <AppShell.Navbar className="app-navbar" data-compact={compactSidebar} p={0}>
        <AgentSidebar compact={compactSidebar} onNavigate={closeNavigation} onCreateAgent={openCreateAgent} onOpenSettings={openSettings} />
        <div
          aria-label="调节侧栏宽度"
          aria-orientation="vertical"
          aria-valuemax={MAX_SIDEBAR_WIDTH}
          aria-valuemin={MIN_SIDEBAR_WIDTH}
          aria-valuenow={sidebarWidth}
          className={
            resizingSidebar ? "sidebar-resize is-dragging" : "sidebar-resize"
          }
          onPointerCancel={endSidebarResize}
          onPointerDown={beginSidebarResize}
          onPointerMove={moveSidebarResize}
          onPointerUp={endSidebarResize}
          onKeyDown={resizeSidebarWithKeyboard}
          tabIndex={0}
          role="separator"
        />
      </AppShell.Navbar>
      <AppShell.Main className="main-pane">
        <ActionIcon
          className="mobile-nav-trigger"
          aria-label="打开 Agent 名册"
          hiddenFrom="sm"
          onClick={toggleNavigation}
          radius="xl"
          size="lg"
          variant="default"
        >
          <IconMenu2 size={19} />
        </ActionIcon>
        <Routes>
          <Route path="/" element={<AgentHome onCreateAgent={openCreateAgent} />} />
          <Route path="/capabilities" element={<CapabilitiesPage />} />
          <Route path="/groups/:groupId" element={<GroupChatPage />} />
          <Route path="/sessions/:sessionId" element={<SessionConversation />} />
          <Route path="/external/new" element={<NewLocalCodexConversation />} />
          <Route path="/external/:identity" element={<ExternalSessionPage />} />
        </Routes>
      </AppShell.Main>
    </AppShell>
    <CreateAgentModal opened={dialog?.kind === "create"} configuring={dialog?.kind === "create-settings"}
      returnedFromSettings={dialog?.kind === "create" && dialog.returnedFromSettings === true}
      connectionId={connectionId} onConfigureModels={openCreateSettings} onClose={() => setDialog(null)}
      onCreated={(id) => {
        setDialog(null);
        closeNavigation();
        navigate(`/sessions/${encodeURIComponent(id)}`);
      }} />
    <SettingsDrawer section={settingsSection} opened={dialog?.kind === "settings" || dialog?.kind === "create-settings"}
      fromCreate={dialog?.kind === "create-settings"} onClose={closeSettings} />
    </BotAvatars>
    </BotIdentities>
  );
}
