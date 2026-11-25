import { Button, Group, Stack, Text } from "@mantine/core";
import { IconAlertCircle, IconRefresh } from "@tabler/icons-react";

import { AttachmentTile, attachmentUrl } from "./AttachmentTile";
import { FileAttachmentTile } from "./FileAttachmentTile";
import { formatMessageTime } from "./messageTime";
import { MarkdownMessage } from "./MarkdownMessage";
import { MessageMenu } from "./ChatPresentation";

const ATTACHMENT_TOKEN = /\[(?:Image|File) #\d+\]/g;

type UserMessageProps = {
  messageId?: string;
  sessionId: string;
  text: string;
  occurredAt: string;
  images: string[];
  files: { attachment_id: string; name: string; size: number }[];
  deliveryState?: "sending" | "failed";
  deliveryError?: string | null;
  retryDisabled?: boolean;
  onRetry?: () => void;
  onRestore?: () => void;
  onDiscard?: () => void;
};

export function UserMessage({
  messageId,
  sessionId,
  text,
  occurredAt,
  images,
  files,
  deliveryState,
  deliveryError,
  retryDisabled = false,
  onRetry,
  onRestore,
  onDiscard,
}: UserMessageProps) {
  const displayText = text.replace(ATTACHMENT_TOKEN, "").trim();
  const time = formatMessageTime(occurredAt);
  const fileCards = files.length === 0 ? null : (
    <Group gap={8} justify="flex-end">
      {files.map((file) => (
        <FileAttachmentTile
          key={file.attachment_id}
          name={file.name}
          size={file.size}
          href={attachmentUrl(sessionId, file.attachment_id)}
        />
      ))}
    </Group>
  );

  const rowClass = deliveryState === "sending"
    ? "user-message-row user-bubble is-sending"
    : deliveryState === "failed"
      ? "user-message-row user-bubble is-failed"
      : "user-message-row user-bubble";

  return (
    <div className="user-message-content">
    <MessageMenu id={deliveryState === undefined ? messageId : undefined} author="你" text={text}>
    <Stack className={rowClass} gap={6} tabIndex={0}>
      {fileCards}
      {images.length === 0 ? null : (
        <Group className="user-attachments" gap={8} justify="flex-end">
          {images.map((attachmentId) => (
            <AttachmentTile
              key={attachmentId}
              large={images.length === 1 && displayText === ""}
              name="图片"
              src={attachmentUrl(sessionId, attachmentId)}
            />
          ))}
        </Group>
      )}
      {displayText === "" ? null : (
        <MarkdownMessage content={displayText} streaming={false} />
      )}
    </Stack>
    </MessageMenu>
    {deliveryState === "sending" ? (
      <Text className="message-time message-delivery-status" c="dimmed" fz={11} role="status">发送中…</Text>
    ) : deliveryState === "failed" ? (
      <div className="message-delivery-failed" role="alert">
        <Group gap={4} wrap="nowrap" className="message-delivery-error">
          <IconAlertCircle size={13} aria-hidden="true" />
          <Text c="red" fz={12}>{deliveryError ?? "消息发送失败，请重试。"}</Text>
        </Group>
        <Group gap={4} wrap="nowrap">
          {onRetry === undefined ? null : (
            <Button size="compact-xs" variant="subtle" color="red" leftSection={<IconRefresh size={12} />}
              disabled={retryDisabled} onClick={onRetry}>重试</Button>
          )}
          {onRestore === undefined ? null : (
            <Button size="compact-xs" variant="subtle" color="gray" onClick={onRestore}>放回输入框</Button>
          )}
          {onDiscard === undefined ? null : (
            <Button size="compact-xs" variant="subtle" color="gray" onClick={onDiscard}>删除</Button>
          )}
        </Group>
      </div>
    ) : (
      <Text className="message-time" component="time" dateTime={occurredAt} c="dimmed" fz={11} title={`发送于 ${time.full}`}>{time.short}</Text>
    )}
    </div>
  );
}
