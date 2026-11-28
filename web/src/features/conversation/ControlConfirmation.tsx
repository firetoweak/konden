import { Button, Group, Paper, Stack, Text } from "@mantine/core";
import { IconCheck, IconX } from "@tabler/icons-react";

import type { ConversationView } from "../../api/contracts";

export function ControlConfirmation({ control, disabled, onDecide }: {
  control: ConversationView["session"]["control_approval"];
  disabled: boolean;
  onDecide: (approved: boolean) => void;
}) {
  if (control === null) {
    return null;
  }
  return (
    <Paper aria-label="待处理" component="section" mb={8} p="sm" radius="md" withBorder>
      <Stack gap="sm">
        <Text fw={600} size="sm">待处理 · 1 项</Text>
        <Stack gap={4}>
          <Text className="pre-wrap" fz={13}>{control.summary}</Text>
          <Text c="dimmed" className="pre-wrap" fz={12}>风险：{control.risk}</Text>
          <Group justify="flex-end" gap="xs">
            <Button color="gray" disabled={disabled} leftSection={<IconX size={14} />}
              onClick={() => onDecide(false)} size="compact-sm" variant="light">
              取消
            </Button>
            <Button color="ember" disabled={disabled} leftSection={<IconCheck size={14} />}
              onClick={() => onDecide(true)} size="compact-sm" variant="light">
              确认
            </Button>
          </Group>
        </Stack>
      </Stack>
    </Paper>
  );
}
