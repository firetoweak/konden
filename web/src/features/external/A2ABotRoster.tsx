import { Group, Stack, Text, Tooltip } from "@mantine/core";
import { IconPlugConnected } from "@tabler/icons-react";

import { useGetA2ABotsQuery } from "../../api/kondenApi";

export function A2ABotRoster({ query, compact }: { query: string; compact: boolean }) {
  const { data = [], error } = useGetA2ABotsQuery();
  const bots = data.filter((bot) => bot.name.toLocaleLowerCase().includes(query));
  if (bots.length === 0 && error === undefined) return null;
  return <section className="roster-section external-roster" aria-label="A2A Bot">
    {compact ? null : <Group className="roster-section-header"><Text fz={11}>A2A Bot</Text></Group>}
    {error === undefined ? null : <Text size="xs" c="dimmed" role="status">无法读取 A2A Bot</Text>}
    <Stack gap={3}>{bots.map((bot) => <Tooltip key={bot.bot_id} label={bot.name}>
      <Group gap={9} wrap="nowrap" justify={compact ? "center" : "flex-start"} aria-label={bot.name} px="xs" py={4}>
        <span className="agent-avatar agent-avatar-small" aria-hidden="true"><IconPlugConnected size={16} /></span>
        {compact ? null : <Text size="sm" truncate>{bot.name}</Text>}
      </Group>
    </Tooltip>)}</Stack>
  </section>;
}
