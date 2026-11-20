import {
  ActionIcon,
  Image,
  Loader,
  UnstyledButton,
} from "@mantine/core";
import { IconX } from "@tabler/icons-react";
import { useState } from "react";
import { ImagePreview } from "./ImagePreview";

type AttachmentTileProps = {
  src: string;
  name: string;
  state?: "uploading" | "done" | "error";
  large?: boolean;
  onRemove?: () => void;
  downloadHref?: string;
};

export function AttachmentTile({
  src,
  name,
  state = "done",
  large = false,
  onRemove,
  downloadHref,
}: AttachmentTileProps) {
  const [opened, setOpened] = useState(false);
  const canOpen = state === "done";
  return (
    <>
      <span
        className={
          large ? "attachment-tile attachment-tile-large" : "attachment-tile"
        }
        data-state={state}
      >
        <UnstyledButton
          aria-label={name}
          className="attachment-tile-button"
          disabled={!canOpen}
          onClick={() => {
            if (canOpen) {
              setOpened(true);
            }
          }}
        >
          <Image alt="" className="attachment-tile-thumb" src={src} />
          {state === "uploading" ? (
            <span className="attachment-tile-overlay">
              <Loader color="white" size={16} />
            </span>
          ) : null}
          {state === "error" ? (
            <span className="attachment-tile-overlay attachment-tile-error">
              上传失败
            </span>
          ) : null}
        </UnstyledButton>
        {onRemove === undefined ? null : (
          <ActionIcon
            aria-label={`Remove ${name}`}
            className="attachment-tile-remove"
            color="gray"
            onClick={onRemove}
            radius="xl"
            size={18}
            variant="filled"
          >
            <IconX size={10} stroke={2.4} />
          </ActionIcon>
        )}
      </span>
      <ImagePreview
        downloadHref={downloadHref}
        name={name}
        onClose={() => setOpened(false)}
        opened={opened}
        src={src}
      />
    </>
  );
}

export function attachmentUrl(sessionId: string, attachmentId: string) {
  return `/api/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(attachmentId)}`;
}
