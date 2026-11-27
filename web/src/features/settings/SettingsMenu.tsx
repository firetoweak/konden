import { ActionIcon, Box, Button, Drawer, Menu } from "@mantine/core";
import { IconAdjustments, IconArrowLeft, IconBrain, IconPlugConnected, IconSettings } from "@tabler/icons-react";
import { useEffect, useState } from "react";
import { ModelSettings } from "../models/ModelSettings";
import { ExternalAccess } from "./ExternalAccess";
import { SharedMemoryPanel } from "./SharedMemoryPanel";

export type SettingsSection = "models" | "memory" | "external";

export function SettingsMenu({ onOpen }: { onOpen: (section: SettingsSection) => void }) {
  return <Menu position="top-start" withinPortal>
    <Menu.Target><ActionIcon aria-label="设置" title="设置" className="settings-trigger" variant="subtle" color="gray" size={32}><IconSettings size={18} /></ActionIcon></Menu.Target>
    <Menu.Dropdown>
      <Menu.Item leftSection={<IconAdjustments size={16} />} onClick={() => onOpen("models")}>模型配置</Menu.Item>
      <Menu.Item leftSection={<IconBrain size={16} />} onClick={() => onOpen("memory")}>共同记忆</Menu.Item>
      <Menu.Item leftSection={<IconPlugConnected size={16} />} onClick={() => onOpen("external")}>Codex 接入</Menu.Item>
    </Menu.Dropdown>
  </Menu>;
}

export function SettingsDrawer({ section, opened, fromCreate, onClose }: {
  section: SettingsSection;
  opened: boolean;
  fromCreate: boolean;
  onClose: () => void;
}) {
  const [visited, setVisited] = useState<ReadonlySet<SettingsSection>>(new Set());
  useEffect(() => {
    if (opened) setVisited((current) => current.has(section) ? current : new Set(current).add(section));
  }, [opened, section]);
  return <Drawer.Root opened={opened} onClose={onClose} position="right" keepMounted
    returnFocus={!fromCreate}
    size={section === "models" ? "min(900px, 100vw)" : "min(560px, 100vw)"}
    padding="lg" className="settings-drawer">
    <Drawer.Overlay />
    <Drawer.Content>
      <Drawer.Header>
        <Drawer.Title>{section === "models" ? "模型配置" : section === "memory" ? "共同记忆" : "Codex 接入"}</Drawer.Title>
        {fromCreate ? <Button variant="subtle" color="gray" size="compact-sm" data-autofocus
          leftSection={<IconArrowLeft size={15} />} onClick={onClose}>返回新建 Bot</Button>
          : <Drawer.CloseButton aria-label="关闭设置" />}
      </Drawer.Header>
      <Drawer.Body>
        {visited.has("models") ? <Box hidden={section !== "models"}>
          <ModelSettings active={opened && section === "models"} onSaveAndReturn={fromCreate && opened ? onClose : undefined} />
        </Box> : null}
        {visited.has("memory") ? <Box hidden={section !== "memory"}><SharedMemoryPanel active={opened && section === "memory"} /></Box> : null}
        {opened && section === "external" ? <ExternalAccess /> : null}
      </Drawer.Body>
    </Drawer.Content>
  </Drawer.Root>;
}
