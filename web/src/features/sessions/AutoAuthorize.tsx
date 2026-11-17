import { Stack, Switch, Text, Tooltip } from "@mantine/core";
import { IconAlertTriangle } from "@tabler/icons-react";
import { useGetAutoAuthorizeQuery, useSetAutoAuthorizeMutation } from "../../api/kondenApi";
import { useAppSelector } from "../../app/hooks";

export function AutoAuthorize({ compact = false }: { compact?: boolean }) {
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const { data: policy } = useGetAutoAuthorizeQuery();
  const [setAutoAuthorize, { isLoading, error }] = useSetAutoAuthorizeMutation();
  return <Stack gap={3} className="sidebar-auto-authorize">
    <Tooltip label="自动放行所有会话的写文件等工具">
      <Switch aria-label="自动放行写文件等工具" label={compact ? undefined : "自动放行"} size="xs"
        checked={policy?.enabled ?? false}
        disabled={connectionId === null || policy === undefined || isLoading}
        onChange={(event) => void setAutoAuthorize({ connectionId: connectionId!, enabled: event.currentTarget.checked })} />
    </Tooltip>
    {error ? compact ? <Tooltip label="自动放行设置失败"><span role="alert" aria-label="自动放行设置失败"><IconAlertTriangle size={16} color="var(--mantine-color-red-6)" /></span></Tooltip> : <Text c="red" fz={11} role="alert">自动放行设置失败</Text> : null}
  </Stack>;
}
