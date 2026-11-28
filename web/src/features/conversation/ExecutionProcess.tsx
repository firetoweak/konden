import {
  Alert,
  Badge,
  Collapse,
  Group,
  Loader,
  Paper,
  Stack,
  Text,
  ThemeIcon,
  Tooltip,
  UnstyledButton,
} from "@mantine/core";
import {
  IconAlertCircle,
  IconAlertTriangle,
  IconCheck,
  IconChevronDown,
  IconChevronRight,
  IconCode,
  IconTool,
} from "@tabler/icons-react";
import { useEffect, useState } from "react";

import type { ToolStatus } from "../../api/contracts";
import { attachmentUrl } from "./AttachmentTile";
import { FileAttachmentTile } from "./FileAttachmentTile";
import { stepHeading } from "./stepHeading";
import { isSubagentTool } from "./subagent";
import { SubagentCallCard } from "./SubagentCallCard";
import type { VisibleStep, VisibleTool } from "./visibleTimeline";
import { MarkdownMessage } from "./MarkdownMessage";
import { ThinkingBlock } from "./ThinkingBlock";

const STATUS_LABEL: Record<ToolStatus, string> = {
  queued: "排队中",
  running: "运行中",
  succeeded: "完成",
  failed: "失败",
  unknown: "中断",
};

const STATUS_COLOR: Record<ToolStatus, string> = {
  queued: "gray",
  running: "ember",
  succeeded: "gray",
  failed: "red",
  unknown: "yellow",
};

interface ExecutionProcessProps {
  steps: VisibleStep[];
  onObserveSubagent?: (commandId: string) => void;
}

export function ExecutionProcess({
  steps,
  onObserveSubagent,
}: ExecutionProcessProps) {
  const [opened, setOpened] = useState(false);
  const running = steps.some(stepStatusIsRunning);
  const tools = steps.flatMap((step) => step.tools);

  return (
    <Paper className={`execution-process${opened ? " is-open" : ""}`} radius="md" withBorder>
      <Tooltip label={`${opened ? "收起" : "查看"}执行过程${running ? " · 运行中" : ""}`} events={{ hover: true, focus: true, touch: false }}>
        <UnstyledButton
          aria-label="执行过程"
          aria-expanded={opened}
          className="execution-toggle"
          onClick={() => setOpened((value) => !value)}
        >
          {running ? <Loader color="ember" size={14} /> : <IconTool aria-hidden="true" size={16} />}
        </UnstyledButton>
      </Tooltip>
      <Collapse expanded={opened}>
        <Stack className="execution-steps" gap={4}>
          {opened ? tools.map((tool) => isSubagentTool(tool.name)
            ? <SubagentCallCard key={tool.commandId} tool={tool} onObserve={onObserveSubagent} />
            : <ToolCard key={tool.commandId} tool={tool} />) : null}
        </Stack>
      </Collapse>
    </Paper>
  );
}

export function StepDisclosure({
  index,
  step,
  onObserveSubagent,
  sessionId,
  expandRunning = false,
}: {
  index: number;
  step: VisibleStep;
  onObserveSubagent?: (commandId: string) => void;
  sessionId?: string;
  expandRunning?: boolean;
}) {
  const status = stepStatus(step);
  const autoOpened = expandRunning && stepStatusIsRunning(step);
  const [opened, setOpened] = useState(autoOpened);

  useEffect(() => {
    // 主会话只由用户展开；子 Agent 观察端展开正在执行的步骤。
    setOpened(autoOpened);
  }, [autoOpened]);

  return (
    <div className="step-panel">
      <Group gap={4} wrap="nowrap">
        <UnstyledButton
          aria-expanded={opened}
          className="step-toggle"
          onClick={() => setOpened((value) => !value)}
          style={{ flex: 1, minWidth: 0 }}
        >
          <Group justify="space-between" wrap="nowrap" gap="xs">
            <Group gap={8} wrap="nowrap" maw="100%" style={{ minWidth: 0 }}>
              {opened ? <IconChevronDown size={13} /> : <IconChevronRight size={13} />}
              <Text c="dimmed" fz={11} w={16} ta="right">
                {index + 1}
              </Text>
              <Text className="step-heading" size="sm" truncate>
                {stepHeading(step)}
              </Text>
            </Group>
            {status === "succeeded" ? null : (
              <Badge color={STATUS_COLOR[status]} size="xs" variant="light">
                {STATUS_LABEL[status]}
              </Badge>
            )}
          </Group>
        </UnstyledButton>
      </Group>
      <Collapse expanded={opened}>
        {opened ? (
          <StepContent step={step} sessionId={sessionId} onObserveSubagent={onObserveSubagent} />
        ) : null}
      </Collapse>
    </div>
  );
}

function StepContent({ step, sessionId, onObserveSubagent }: {
  step: VisibleStep;
  sessionId?: string;
  onObserveSubagent?: (commandId: string) => void;
}) {
  return <Stack className="step-content" gap="xs">
    {step.thinking === null ? null : <ThinkingBlock streaming={step.thinkingPending} text={step.thinking} />}
    {step.text === null ? null : <MarkdownMessage content={step.text} streaming={step.pending} />}
    {step.files.length === 0 || sessionId === undefined ? null : (
      <Group gap={8}>
        {step.files.map((file) => (
          <FileAttachmentTile
            key={file.attachment_id}
            name={file.name}
            size={file.size}
            href={attachmentUrl(sessionId, file.attachment_id)}
          />
        ))}
      </Group>
    )}
    {step.tools.map((tool) => isSubagentTool(tool.name)
      ? <SubagentCallCard key={tool.commandId} tool={tool} onObserve={onObserveSubagent} />
      : <ToolCard key={tool.commandId} tool={tool} />)}
  </Stack>;
}

function ToolCard({ tool }: { tool: VisibleTool }) {
  const hasArguments = Object.keys(tool.arguments).length > 0;
  const [detailsOpen, setDetailsOpen] = useState(false);

  return (
    <Paper className="tool-card" px="sm" py={6} radius="sm" withBorder>
      <UnstyledButton
        className="tool-toggle"
        onClick={() => {
          if (hasArguments) {
            setDetailsOpen((value) => !value);
          }
        }}
      >
        <Group justify="space-between" wrap="nowrap" gap="xs">
          <Group gap={8} wrap="nowrap" style={{ minWidth: 0 }}>
            <ThemeIcon
              color={STATUS_COLOR[tool.status]}
              radius="sm"
              size={22}
              variant="light"
            >
              {tool.status === "running" ? (
                <Loader color="ember" size={11} />
              ) : tool.status === "failed" ? (
                <IconAlertCircle size={13} />
              ) : tool.status === "unknown" ? (
                <IconAlertTriangle size={13} />
              ) : (
                <IconCode size={13} />
              )}
            </ThemeIcon>
            <Text ff="monospace" fw={600} size="sm" truncate>
              {tool.name === "update_plan"
                ? tool.arguments.plan === null ? "结束当前计划" : "更新计划"
                : tool.name}
            </Text>
          </Group>
          <Badge
            color={STATUS_COLOR[tool.status]}
            leftSection={
              tool.status === "succeeded" ? <IconCheck size={10} /> : undefined
            }
            size="xs"
            variant="light"
          >
            {STATUS_LABEL[tool.status]}
          </Badge>
        </Group>
      </UnstyledButton>
      {detailsOpen && hasArguments ? (
        <Text c="dimmed" className="pre-wrap" ff="monospace" fz={11} mt={6}>
          {formatArguments(tool.arguments)}
        </Text>
      ) : null}
      {tool.error === null ? null : (
        <Alert color={STATUS_COLOR[tool.status]} mt="xs" py="xs" variant="light">
          <Text ff="monospace" fz={12} className="pre-wrap">
            {tool.error}
          </Text>
        </Alert>
      )}
    </Paper>
  );
}

function formatArguments(arguments_: Record<string, unknown>) {
  return JSON.stringify(arguments_, null, 2);
}

function stepStatus(step: VisibleStep): ToolStatus {
  if (step.pending || step.tools.some((tool) => tool.status === "running")) {
    return "running";
  }
  if (step.tools.some((tool) => tool.status === "queued")) {
    return "queued";
  }
  if (step.tools.some((tool) => tool.status === "unknown")) {
    return "unknown";
  }
  if (step.tools.some((tool) => tool.status === "failed")) {
    return "failed";
  }
  return "succeeded";
}

function stepStatusIsRunning(step: VisibleStep): boolean {
  const status = stepStatus(step);
  return status === "queued" || status === "running";
}
