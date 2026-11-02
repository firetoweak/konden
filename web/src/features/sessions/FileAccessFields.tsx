import { ActionIcon, Button, Group, Radio, Stack, Text, TextInput } from "@mantine/core";
import { IconFolderOpen, IconPlus, IconX } from "@tabler/icons-react";
import { useState } from "react";
import type { FileAccess } from "../../api/contracts";
import { useSelectWorkspaceDirectoryMutation } from "../../api/kondenApi";

export type FileAccessDraft = { mode: FileAccess["mode"]; directories: string[] };
export const accessDraft = (value: FileAccess): FileAccessDraft => ({
  mode: value.mode, directories: value.mode === "directories" ? value.directories : [],
});
export const accessConfig = (value: FileAccessDraft): FileAccess => value.mode === "full"
  ? { mode: "full" } : { mode: "directories", directories: value.directories.map((path) => path.trim()) };
export const validAccessDraft = (value: FileAccessDraft) => value.mode === "full"
  || value.directories.every((path) => path.trim() !== "");

export function FileAccessFields({ value, onChange, disabled = false, onSelectingChange }: {
  value: FileAccessDraft; onChange: (value: FileAccessDraft) => void;
  disabled?: boolean; onSelectingChange?: (selecting: boolean) => void;
}) {
  const [selectDirectory, selection] = useSelectWorkspaceDirectoryMutation();
  const [error, setError] = useState<string | null>(null);
  function changeDirectory(index: number, path: string) {
    onChange({ ...value, directories: value.directories.map((entry, i) => i === index ? path : entry) });
  }
  async function chooseDirectory(index: number) {
    setError(null);
    onSelectingChange?.(true);
    try {
      const { directory } = await selectDirectory().unwrap();
      if (directory !== null) changeDirectory(index, directory.path);
    } catch (cause) {
      setError(typeof cause === "string" ? cause : "选择失败，可以直接填写目录路径。");
    } finally {
      onSelectingChange?.(false);
    }
  }
  const locked = disabled || selection.isLoading;
  return <Stack gap="xs">
    <Radio.Group label="可用目录" value={value.mode}
      onChange={(mode) => onChange({ ...value, mode: mode as FileAccessDraft["mode"] })}>
      <Stack gap="xs" mt={6}>
        <Radio value="directories" label="指定目录" disabled={locked} />
        <Radio value="full" label="全部目录" disabled={locked} />
      </Stack>
    </Radio.Group>
    {value.mode === "directories" ? <>
        {value.directories.map((path, index) => <Group key={index} gap={4} wrap="nowrap">
          <TextInput aria-label={`访问目录 ${index + 1}`} placeholder="本机目录的绝对路径" value={path}
            onChange={(event) => changeDirectory(index, event.currentTarget.value)} disabled={locked}
            style={{ flex: 1, minWidth: 0 }} />
          <ActionIcon variant="subtle" color="gray" aria-label={`选择访问目录 ${index + 1}`}
            disabled={locked} onClick={() => void chooseDirectory(index)}><IconFolderOpen size={17} /></ActionIcon>
          <ActionIcon variant="subtle" color="gray" aria-label={`移除访问目录 ${index + 1}`}
            disabled={locked} onClick={() => onChange({ ...value, directories: value.directories.filter((_, i) => i !== index) })}>
            <IconX size={15} />
          </ActionIcon>
        </Group>)}
        <Button variant="subtle" color="gray" size="compact-xs" leftSection={<IconPlus size={14} />}
          disabled={locked} style={{ alignSelf: "flex-start" }}
          onClick={() => onChange({ ...value, directories: [...value.directories, ""] })}>添加目录</Button>
      </> : null}
    {error === null ? null : <Text size="xs" c="red" role="alert">{error}</Text>}
  </Stack>;
}
