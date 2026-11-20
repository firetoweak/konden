import { ActionIcon, Anchor, Loader, Modal, Text } from "@mantine/core";
import { useElementSize, useMergedRef } from "@mantine/hooks";
import { IconDownload, IconMinus, IconPlus, IconZoomReset } from "@tabler/icons-react";
import { useLayoutEffect, useRef, useState } from "react";
import classes from "./ImagePreview.module.css";

type ImagePreviewProps = {
  name: string;
  src: string;
  downloadHref?: string;
  opened: boolean;
  onClose: () => void;
};

const ZOOM_STEP = 1.25;
const MAX_ZOOM = 4;

export function ImagePreview({ name, src, downloadHref, opened, onClose }: ImagePreviewProps) {
  return (
    <Modal.Root
      centered
      classNames={{ content: classes.content }}
      opened={opened}
      onClose={onClose}
      radius="lg"
      size="100%"
      styles={{ header: { paddingTop: 6, paddingBottom: 6 } }}
      xOffset="clamp(8px, 4vw, 64px)"
      yOffset="clamp(8px, 3vh, 28px)"
      zIndex={300}
    >
      <Modal.Overlay />
      <Modal.Content>
        <ImagePreviewContent key={src} name={name} src={src} downloadHref={downloadHref} />
      </Modal.Content>
    </Modal.Root>
  );
}

function ImagePreviewContent({ name, src, downloadHref }: Pick<ImagePreviewProps, "name" | "src" | "downloadHref">) {
  const { ref: observeViewport, width, height } = useElementSize<HTMLDivElement>();
  const viewportRef = useRef<HTMLDivElement>(null);
  const viewport = useMergedRef(observeViewport, viewportRef);
  const zoomCenter = useRef<{ x: number; y: number } | null>(null);
  const dragStart = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const [imageSize, setImageSize] = useState<{ width: number; height: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const [zoom, setZoom] = useState<number | null>(null);

  const fit = imageSize === null ? 0 : Math.min(1, width / imageSize.width, height / imageSize.height);
  const scale = Math.max(fit, zoom ?? fit);
  const imageWidth = (imageSize?.width ?? 0) * scale;
  const imageHeight = (imageSize?.height ?? 0) * scale;
  const pannable = scale > fit && (imageWidth > width || imageHeight > height);

  function changeZoom(next: number | null) {
    const viewport = viewportRef.current!;
    zoomCenter.current = {
      x: (viewport.scrollLeft + viewport.clientWidth / 2) / viewport.scrollWidth,
      y: (viewport.scrollTop + viewport.clientHeight / 2) / viewport.scrollHeight,
    };
    setZoom(next);
  }

  useLayoutEffect(() => {
    const center = zoomCenter.current;
    if (center === null) return;
    const viewport = viewportRef.current!;
    viewport.scrollLeft = center.x * viewport.scrollWidth - viewport.clientWidth / 2;
    viewport.scrollTop = center.y * viewport.scrollHeight - viewport.clientHeight / 2;
    zoomCenter.current = null;
  }, [scale]);

  return (
    <>
      <Modal.Header className={classes.header}>
        <Modal.Title className={classes.title} title={name}>{name}</Modal.Title>
        <div className={classes.controls}>
          <div className={classes.zoom}>
            <ActionIcon
              aria-label="缩小"
              title="缩小"
              color="gray"
              variant="subtle"
              size="lg"
              disabled={fit === 0 || scale <= fit}
              onClick={() => changeZoom(scale <= fit * ZOOM_STEP ? null : scale / ZOOM_STEP)}
            >
              <IconMinus size={18} />
            </ActionIcon>
            <Text className={classes.percent} size="sm" aria-live="polite">
              {fit === 0 ? "—" : `${Math.max(1, Math.round(scale * 100))}%`}
            </Text>
            <ActionIcon
              aria-label="放大"
              title="放大"
              color="gray"
              variant="subtle"
              size="lg"
              disabled={fit === 0 || scale >= MAX_ZOOM}
              onClick={() => changeZoom(Math.min(MAX_ZOOM, scale * ZOOM_STEP))}
            >
              <IconPlus size={18} />
            </ActionIcon>
            <ActionIcon
              aria-label="适应窗口"
              title="适应窗口"
              color="gray"
              variant="subtle"
              size="lg"
              disabled={scale <= fit}
              onClick={() => changeZoom(null)}
            >
              <IconZoomReset size={18} />
            </ActionIcon>
          </div>
          {downloadHref === undefined ? null : (
            <Anchor className={classes.download} href={downloadHref} download={name} size="sm">
              <IconDownload size={17} aria-hidden="true" />
              下载原图
            </Anchor>
          )}
          <Modal.CloseButton aria-label="关闭图片预览" size="lg" data-autofocus />
        </div>
      </Modal.Header>
      <Modal.Body className={classes.body}>
        <div
          ref={viewport}
          className={classes.viewport}
          data-pannable={pannable || undefined}
          role="region"
          aria-label="图片查看区域"
          tabIndex={0}
          onPointerDown={(event) => {
            if (!pannable || event.pointerType !== "mouse" || event.button !== 0) return;
            const viewport = event.currentTarget;
            dragStart.current = { x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
            viewport.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            const start = dragStart.current;
            if (start === null || event.pointerType !== "mouse") return;
            event.currentTarget.scrollLeft = start.left + start.x - event.clientX;
            event.currentTarget.scrollTop = start.top + start.y - event.clientY;
          }}
          onLostPointerCapture={() => { dragStart.current = null; }}
        >
          <div className={classes.stage}>
            {failed ? <Text c="dimmed" role="status">图片加载失败</Text> : (
              <img
                className={classes.image}
                alt={name}
                src={src}
                draggable={false}
                style={{ width: imageWidth, height: imageHeight }}
                onError={() => setFailed(true)}
                onLoad={({ currentTarget }) => setImageSize({
                  width: currentTarget.naturalWidth,
                  height: currentTarget.naturalHeight,
                })}
              />
            )}
            {imageSize === null && !failed ? <Loader size="sm" aria-label="正在加载图片" /> : null}
          </div>
        </div>
      </Modal.Body>
    </>
  );
}
