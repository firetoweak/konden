import { useEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type PointerEvent } from "react";
import { IconMessageFilled } from "@tabler/icons-react";
import type { TraceEntry } from "../../api/traceContracts";
import { cacheLabel, cacheMiss, clamp, dated, fitWindow, interval, lane, lanes, overlaps, point, span,
  type Mark, type Range, type TraceAxis } from "./traceAxis";

type Props = {
  axis: TraceAxis;
  window: Range | null;
  setWindow: (window: Range | null) => void;
  range: Range | null;
  setRange: (range: Range | null) => void;
  selected: string | undefined;
  onSelect: (entry: TraceEntry) => void;
  tool: string;
  status: TraceStatusFilter;
};

export type TraceStatusFilter = "all" | "failed" | "unknown";
export const statusLabel = { passed: "已返回", failed: "失败", unknown: "结果未知", pending: "待派发", not_applicable: "不适用" };

export function toolMatches(entry: TraceEntry, tool: string, status: TraceStatusFilter) {
  return entry.kind !== "tool" || ((!tool || entry.title === tool) && (status === "all" || entry.status === status));
}

function position(plot: Range, bounds: Range): CSSProperties {
  const start = clamp(point(plot[0], bounds), [0, 1]);
  const end = clamp(point(plot[1], bounds), [0, 1]);
  return { left: `${start * 100}%`, width: `${Math.max(0, end - start) * 100}%` };
}

function rangeLabel(range: Range) {
  return `${dated(range[0], false, true)} – ${dated(range[1], false, true)}`;
}

function markLabel({ entry }: Mark) {
  const suffix = entry.kind === "input" ? (entry.user ? "用户输入 · 输入详情" : "输入详情")
    : entry.kind === "model" ? cacheLabel(entry)
      : entry.status === undefined ? entry.preview : statusLabel[entry.status];
  return `${entry.title}${suffix ? ` · ${suffix}` : ""} · ${dated(entry.start, false, true)}`;
}

type DrawMark = { mark: Mark; failed: number; count: number; uncertain: number };

function densityMarks(marks: Mark[], bounds: Range, width: number, selected: string | undefined, range: Range | null): DrawMark[] {
  const columns = Math.max(1, Math.ceil(width / 4));
  const groups = new Map<string, { mark: Mark; priority: number; failed: number; count: number; uncertain: number }>();
  const visible: DrawMark[] = [];
  for (const mark of marks) {
    const entry = mark.entry;
    if (entry.kind === "subagent" || entry.kind === "catalog") {
      visible.push({ mark, count: 0, failed: 0, uncertain: 0 });
      continue;
    }
    const column = clamp(Math.floor(point(mark.anchor, bounds) * columns), [0, columns - 1]);
    const key = `${lane[entry.kind]}:${column}`;
    const priority = (range === null || overlaps(interval(entry), range) ? 4 : 0)
      + (entry.id === selected ? 3 : entry.user ? 2 : cacheMiss(entry)?.tokens ? 1 : 0);
    const bucket = groups.get(key) ?? { mark, priority, failed: 0, count: 0, uncertain: 0 };
    if (priority > bucket.priority || (priority === bucket.priority && span(mark.plot) > span(bucket.mark.plot))) {
      bucket.mark = mark;
      bucket.priority = priority;
    }
    bucket.count++;
    if (entry.status === "failed") bucket.failed++;
    else if (entry.status !== undefined && entry.status !== "passed") bucket.uncertain++;
    groups.set(key, bucket);
  }
  return [...visible, ...groups.values()].sort((a, b) => a.mark.anchor - b.mark.anchor);
}

function layerTools(marks: Mark[]) {
  const ends: number[] = [];
  const rows = new Map<string, number>();
  for (const { entry, plot } of marks) {
    if (lane[entry.kind] !== 2) continue;
    const index = ends.findIndex((last) => last <= plot[0]);
    const row = index === -1 ? ends.length : index;
    ends[row] = plot[1];
    rows.set(entry.id, row);
  }
  return { rows, count: Math.max(1, ends.length) };
}

type Drag = { pointer: number; mode: "range" | "pan" | "start" | "end"; x: number; start: number; bounds: Range; range: Range | null; moved: boolean };

export function TraceChart({ axis, window: viewport, setWindow, range, setRange, selected, onSelect, tool, status }: Props) {
  const plot = useRef<HTMLDivElement>(null);
  const scrollbar = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);
  const suppressClick = useRef(false);
  const [width, setWidth] = useState(900);
  const [preview, setPreview] = useState<Range | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const keyboardStart = useRef<number | null>(null);
  const bounds = viewport ? fitWindow(viewport[0], span(viewport), axis.extent) : axis.extent;
  const length = span(bounds);
  const fullLength = span(axis.extent);
  const zoomed = length < fullLength - 0.00001;
  const brush = preview ?? range;
  const rangePlot: Range | null = brush ? [axis.toPlot(brush[0]), axis.toPlot(brush[1])] : null;
  const allMarks = useMemo(() => axis.marks.filter(({ entry }) => toolMatches(entry, tool, status)), [axis, tool, status]);
  const visible = useMemo(() => allMarks.filter(({ plot }) => overlaps(plot, [bounds[0], bounds[1]])), [allMarks, bounds[0], bounds[1]]);
  const dense = visible.length > Math.max(22, Math.floor(width / 12)) && length > 0.5;
  const sampled = useMemo(() => dense && visible.length > width
    ? densityMarks(visible, [bounds[0], bounds[1]], width, selected, range)
    : visible.map((mark) => ({ mark, count: 0, failed: 0, uncertain: 0 })), [dense, visible, width, bounds[0], bounds[1], selected, range]);
  const layers = useMemo(() => layerTools(sampled.map(({ mark }) => mark)), [sampled]);

  useEffect(() => {
    const node = plot.current;
    if (node === null) return;
    const measure = () => setWidth(node.clientWidth || 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  function setViewport(start: number, size: number) {
    const next = fitWindow(start, size, axis.extent);
    setWindow(span(next) >= fullLength - 0.00001 ? null : next);
  }

  function zoom(factor: number, fraction: number = 0.5) {
    const size = length / factor;
    setViewport(bounds[0] + fraction * (length - size), size);
  }

  useEffect(() => {
    const node = plot.current;
    if (!node || !allMarks.length) return;
    function wheel(event: WheelEvent) {
      event.preventDefault();
      const rect = node!.getBoundingClientRect();
      if (Math.abs(event.deltaX) > Math.abs(event.deltaY)) {
        setViewport(bounds[0] + event.deltaX * length / rect.width, length);
      } else {
        const fraction = clamp((event.clientX - rect.left) / rect.width, [0, 1]);
        zoom(event.deltaY < 0 ? 1.35 : 1 / 1.35, fraction);
      }
    }
    node.addEventListener("wheel", wheel, { passive: false });
    return () => node.removeEventListener("wheel", wheel);
  });

  useEffect(() => {
    const node = scrollbar.current;
    if (node === null || !zoomed) return;
    const offset = (bounds[0] - axis.extent[0]) / (fullLength - length) * (node.scrollWidth - node.clientWidth);
    if (Math.abs(node.scrollLeft - offset) >= 1) node.scrollLeft = offset;
  }, [width, bounds[0], fullLength, length, axis.extent, zoomed]);

  function fromPointer(event: PointerEvent, inBounds: Range) {
    const rect = plot.current!.getBoundingClientRect();
    return inBounds[0] + clamp((event.clientX - rect.left) / rect.width, [0, 1]) * span(inBounds);
  }

  function startDrag(event: PointerEvent, mode: Drag["mode"]) {
    if (!allMarks.length || event.button !== (mode === "pan" ? 2 : 0)) return;
    suppressClick.current = false;
    drag.current = { pointer: event.pointerId, mode, x: event.clientX, start: fromPointer(event, bounds), bounds, range, moved: false };
    if (mode !== "range") {
      event.stopPropagation();
      plot.current!.setPointerCapture(event.pointerId);
    }
  }

  function moveDrag(event: PointerEvent<HTMLDivElement>) {
    const active = drag.current;
    if (active === null || active.pointer !== event.pointerId) return;
    if (active.mode === "range" && !active.moved && Math.abs(event.clientX - active.x) < 4) return;
    if (!active.moved) event.currentTarget.setPointerCapture(event.pointerId);
    active.moved = true;
    if (active.mode === "pan") {
      setViewport(active.bounds[0] - (event.clientX - active.x) * span(active.bounds) / width, span(active.bounds));
    } else {
      setPreview(dragRange(active, event));
    }
  }

  function dragRange(active: Drag, event: PointerEvent): Range {
    const time = axis.toTime(fromPointer(event, active.bounds));
    if (active.mode === "start" && active.range) return [Math.min(time, active.range[1]), active.range[1]];
    if (active.mode === "end" && active.range) return [active.range[0], Math.max(time, active.range[0])];
    const start = axis.toTime(active.start);
    return [Math.min(start, time), Math.max(start, time)];
  }

  function finishDrag(event: PointerEvent<HTMLDivElement>) {
    const active = drag.current;
    if (active === null || active.pointer !== event.pointerId) return;
    drag.current = null;
    if (active.moved) {
      suppressClick.current = true;
      if (active.mode !== "pan") setRange(dragRange(active, event));
    }
    setPreview(null);
  }

  function cancelDrag() {
    drag.current = null;
    setPreview(null);
  }

  function chartKey(event: KeyboardEvent) {
    if (!allMarks.length) return;
    const keys = ["+", "=", "-", "Home", "ArrowLeft", "ArrowRight", "Enter", "Escape"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const current = clamp(cursor ?? bounds[0] + length / 2, bounds);
    if (event.key === "+" || event.key === "=") zoom(1.5, point(current, bounds));
    else if (event.key === "-") zoom(1 / 1.5, point(current, bounds));
    else if (event.key === "Home") { setWindow(null); setCursor(null); }
    else if (event.key === "Escape") { setRange(null); keyboardStart.current = null; setPreview(null); }
    else if (event.key === "Enter") {
      const origin = keyboardStart.current;
      if (origin === null) setRange([axis.toTime(current), axis.toTime(current)]);
      else setRange([axis.toTime(Math.min(origin, current)), axis.toTime(Math.max(origin, current))]);
      keyboardStart.current = null;
      setPreview(null);
    } else {
      const next = clamp(current + (event.key === "ArrowLeft" ? -1 : 1) * length / 40, bounds);
      setCursor(next);
      if (event.shiftKey) {
        keyboardStart.current ??= current;
        const origin = keyboardStart.current;
        setPreview([axis.toTime(Math.min(origin, next)), axis.toTime(Math.max(origin, next))]);
      }
    }
  }

  function showSelection() {
    if (!range || !rangePlot) return;
    const margin = Math.max(0.25, span(rangePlot) * 0.15);
    setViewport(rangePlot[0] - margin, span(rangePlot) + 2 * margin);
  }

  const ticks = useMemo(() => {
    const model = visible.filter(({ entry, anchor }) => entry.kind === "model" && anchor >= bounds[0] && anchor <= bounds[1]);
    if (!model.length) return [];
    const fractions = width < 550 ? [0, 0.5, 1] : [0, 0.25, 0.5, 0.75, 1];
    return [...new Set(fractions.map((fraction) => {
      const target = bounds[0] + fraction * length;
      return model.reduce((best, mark) => Math.abs(mark.anchor - target) < Math.abs(best.anchor - target) ? mark : best);
    }))];
  }, [visible, width, bounds[0], bounds[1], length]);
  const zoneCount = width < 550 ? 6 : 18;

  return <section className="trace-overview" aria-label="执行顺序轨迹">
    <div className="trace-chart">
      <div className="trace-lanes">{lanes.map((name) => <span key={name}>{name}</span>)}</div>
      <div className="trace-plot" ref={plot}
        onPointerDown={(event) => startDrag(event, event.button === 2 ? "pan" : "range")}
        onPointerMove={moveDrag} onPointerUp={finishDrag} onPointerCancel={cancelDrag}
        onContextMenu={(event) => event.preventDefault()}
        onClickCapture={(event) => { if (event.detail && suppressClick.current) { suppressClick.current = false; event.stopPropagation(); } }}>
        <button type="button" className="trace-brush" aria-label="选择时间。拖动框选，按住 Shift 用方向键扩展，回车确认。加号或减号缩放，Home 显示全会话。"
          disabled={!allMarks.length} onKeyDown={chartKey} onBlur={() => { setCursor(null); setPreview(null); keyboardStart.current = null; }} />
        {axis.gaps.filter(({ plot: gap }) => overlaps(gap, bounds) && span(gap) * width / length >= 1).map(({ plot: gap, time }) =>
          <span key={gap[0]} className="trace-gap" style={position(gap, bounds)} title={`无记录区段 · ${rangeLabel(time)}`}>
            {span(gap) * width / length >= 10 ? "//" : ""}</span>)}
        {dense && Array.from({ length: zoneCount }, (_, index) => {
          const zone: Range = [bounds[0] + length * index / zoneCount, bounds[0] + length * (index + 1) / zoneCount];
          if (!visible.some((mark) => overlaps(mark.plot, zone))) return null;
          const label = `放大 ${rangeLabel([axis.toTime(zone[0]), axis.toTime(zone[1])])}`;
          return <button key={index} type="button" className="trace-zone" style={position(zone, bounds)} title={label} aria-label={label}
            onClick={() => setViewport(zone[0], span(zone))} />;
        })}
        {sampled.map(({ mark, ...mix }) => {
          const entry = mark.entry;
          const active = selected === entry.id;
          const muted = brush !== null && !overlaps(interval(entry), brush);
          const layer = layers.rows.get(entry.id);
          const miss = cacheMiss(entry);
          const unreported = entry.kind === "model" && miss === null;
          const mixed = mix.count > 1 && (mix.failed + mix.uncertain > 0) && !active;
          const { left, width: size } = position(mark.plot, bounds);
          const style = { "--mark-x": left, "--mark-width": size, "--lane": lane[entry.kind],
            "--call-row": layer === undefined ? undefined : Math.min(layer, 4), "--call-height": Math.min(layers.count, 5),
            "--miss": miss?.ratio ? `max(1px, ${miss.ratio * 100}%)` : "0%",
            "--failed": mixed ? `${mix.failed / mix.count * 100}%` : undefined,
            "--uncertain": mixed ? `${(mix.failed + mix.uncertain) / mix.count * 100}%` : undefined,
          } as CSSProperties;
          const className = [
            "trace-mark", entry.kind, entry.status, active && "selected", muted && "muted",
            entry.user && "has-user", mixed && "mixed", unreported && "cache-unreported",
          ].filter(Boolean).join(" ");
          const content = entry.user ? <IconMessageFilled size={11} aria-hidden="true" /> : entry.kind === "subagent" ? <span>↳</span> : null;
          if (dense && !active && entry.kind !== "subagent" && entry.kind !== "catalog") {
            return <span key={entry.id} className={className} style={style} aria-hidden="true">{content}</span>;
          }
          const title = markLabel(mark);
          return <button type="button" key={entry.id} className={className} style={style} aria-label={title} title={title}
            aria-pressed={active} aria-disabled={muted || undefined} onClick={() => { if (!muted) onSelect(entry); }}>{content}</button>;
        })}
        {rangePlot && overlaps(rangePlot, bounds) && <span className="trace-selection" style={position(rangePlot, bounds)} aria-hidden="true" />}
        {cursor !== null && <span className="trace-cursor" style={{ left: `${point(clamp(cursor, bounds), bounds) * 100}%` }} aria-hidden="true" />}
        {rangePlot && brush && (["start", "end"] as const).map((edge, index) => {
          const x = point(rangePlot[index], bounds);
          if (x < 0 || x > 1) return null;
          const name = `调整${edge === "start" ? "开始" : "结束"}时间`;
          const adjust = (event: KeyboardEvent) => {
            if (!range || !["ArrowLeft", "ArrowRight"].includes(event.key)) return;
            event.preventDefault();
            const other = rangePlot[1 - index];
            const limits: Range = index === 0 ? [bounds[0], other] : [other, bounds[1]];
            const next = axis.toTime(clamp(rangePlot[index] + (event.key === "ArrowLeft" ? -1 : 1) * length / 80, limits));
            setRange(index === 0 ? [next, range[1]] : [range[0], next]);
          };
          return <button key={edge} type="button" className={`trace-handle ${edge}${span(rangePlot) * width / length < 24 ? " tight" : ""}`}
            style={{ left: `${x * 100}%` }} aria-label={`${name} ${dated(brush[index], false, true)}`} title={name}
            onPointerDown={(event) => startDrag(event, edge)} onKeyDown={adjust} />;
        })}
      </div>
      <div className="trace-cache-legend">
        <span><i />未命中</span>
        <span><i className="cache-unreported" />无统计</span>
      </div>
      <div className="trace-ticks" aria-hidden="true">{ticks.map(({ entry, anchor }) => <span key={entry.id}
        style={{ left: `${point(anchor, bounds) * 100}%` }}>第 {entry.step} 步</span>)}</div>
      {zoomed && <div className="trace-horizontal-scroll" ref={scrollbar} tabIndex={0} role="region" aria-label="横向滚动轨迹"
        onScroll={(event) => {
          const node = event.currentTarget;
          const maximum = node.scrollWidth - node.clientWidth;
          if (!maximum) return;
          const offset = (bounds[0] - axis.extent[0]) / (fullLength - length) * maximum;
          if (Math.abs(offset - node.scrollLeft) >= 1) setViewport(axis.extent[0] + node.scrollLeft / maximum * (fullLength - length), length);
        }}><div style={{ width: `${Math.min(1000, fullLength / length) * 100}%` }} /></div>}
    </div>
    {range && <div className="trace-range-status"><span>{rangeLabel(range)}</span>
      <button type="button" onClick={showSelection}>定位选区</button><button type="button" onClick={() => setRange(null)}>清除选区</button></div>}
  </section>;
}
