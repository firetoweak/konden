import { Button } from "@mantine/core";
import { IconExternalLink } from "@tabler/icons-react";

export function CodexDesktopLink({ threadId }: { threadId: string }) {
  return <Button component="a" href={`codex://threads/${threadId}`} variant="subtle" size="compact-xs"
    leftSection={<IconExternalLink size={13} />}>在 Desktop 打开</Button>;
}
