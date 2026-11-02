import { Alert, Button, Group, Stack, Text } from "@mantine/core";
import { useState } from "react";
import { kondenApi, useGetFileAccessQuery, useSaveFileAccessMutation } from "../../api/kondenApi";
import { useAppDispatch } from "../../app/hooks";
import { accessConfig, accessDraft, FileAccessFields, validAccessDraft, type FileAccessDraft } from "../sessions/FileAccessFields";

export function FileAccessPanel({ sessionId, connectionId, active }: {
  sessionId: string; connectionId: string | null; active: boolean;
}) {
  const query = useGetFileAccessQuery(sessionId, { skip: !active, refetchOnMountOrArgChange: true });
  const dispatch = useAppDispatch();
  const [save, saving] = useSaveFileAccessMutation();
  const [draft, setDraft] = useState<FileAccessDraft | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [saved, setSaved] = useState(false);
  const data = query.currentData;
  const saveError: unknown = saving.error;
  if (data === undefined) return <Stack gap="xs">
    <Text size="xs" c="dimmed">可用目录</Text>
    {query.isError ? <><Text size="xs" role="alert">读取失败</Text><Button variant="subtle" size="compact-xs" onClick={() => void query.refetch()}>重试读取可用目录</Button></>
      : <Text size="xs" c="dimmed">正在读取…</Text>}
  </Stack>;
  const value = draft ?? accessDraft(data);
  const changed = JSON.stringify(accessConfig(value)) !== JSON.stringify(data);
  return <Stack gap="xs">
    <FileAccessFields value={value} disabled={saving.isLoading || connectionId === null}
      onSelectingChange={setSelecting} onChange={(next) => { setDraft(next); setSaved(false); saving.reset(); }} />
    {saveError === undefined ? null : <Alert color="red" title="无法保存可用目录">
      {typeof saveError === "string" ? saveError : "保存失败，请重试。"}
    </Alert>}
    <Group gap="xs">
      <Button variant="light" size="compact-xs" loading={saving.isLoading}
        disabled={connectionId === null || selecting || !changed || !validAccessDraft(value)}
        onClick={async () => {
          if (connectionId === null) return;
          try {
            const result = await save({ connectionId, sessionId, config: accessConfig(value) }).unwrap();
            dispatch(kondenApi.util.updateQueryData("getFileAccess", sessionId, () => result));
            setDraft(null);
            setSaved(true);
          } catch { /* The mutation keeps the error and the draft for retry. */ }
        }}>保存可用目录</Button>
      {saved ? <Text size="xs" role="status">已保存</Text> : null}
    </Group>
  </Stack>;
}
