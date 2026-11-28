import type { TraceEntry } from "../../api/traceContracts";

export type Range = [number, number];
export type Mark = { entry: TraceEntry; anchor: number; plot: Range };
export const minimumView = 0.25;
export const lanes = ["输入", "模型", "工具", "Compact", "Subagent"];
export const lane: Record<TraceEntry["kind"], number> = { input: 0, user: 0, model: 1, tool: 2, catalog: 2, compact: 3, subagent: 4 };
const widths: Record<TraceEntry["kind"], number> = { input: 1.25, user: 1.25, model: 3.2, tool: 1, catalog: 0.9, compact: 1.8, subagent: 1 };

export const span = ([start, end]: Range) => end - start;
export const overlaps = ([start, end]: Range, range: Range) => start <= range[1] && end >= range[0];
export const clamp = (value: number, [min, max]: Range) => Math.max(min, Math.min(max, value));
export const interval = (entry: TraceEntry): Range => [entry.start, Math.max(entry.start, entry.end ?? entry.start)];
export const point = (position: number, bounds: Range) => (position - bounds[0]) / span(bounds);

const calendar = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" });
const shortDate = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit" });
const clock = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const tenths = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 1, hourCycle: "h23" });
export function dated(value: number, full = false, precise = false) {
  return `${(full ? calendar : shortDate).format(value * 1000)} ${(precise ? tenths : clock).format(value * 1000)}`;
}

export function elapsed([start, end]: Range) {
  const duration = Math.max(0, end - start);
  if (duration < 1) return `${Math.round(duration * 1000)} 毫秒`;
  return `${new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(duration)} 秒`;
}

export function cacheMiss(entry: TraceEntry) {
  const input = entry.usage?.input_tokens;
  const cached = entry.usage?.cached_input_tokens;
  if (input === undefined || cached === undefined || input < 0 || cached < 0 || cached > input) return null;
  return { tokens: input - cached, ratio: input === 0 ? 0 : (input - cached) / input };
}

const percent = new Intl.NumberFormat("zh-CN", { style: "percent", maximumFractionDigits: 1 });
export function cacheLabel(entry: TraceEntry) {
  const miss = cacheMiss(entry);
  return miss === null ? "缓存命中未知" : `缓存未命中 ${miss.tokens.toLocaleString("zh-CN")} tokens（${percent.format(miss.ratio)}）`;
}

type Anchor = { time: number; position: number; width: number; activeUntil: number };

export function buildAxis(entries: TraceEntry[], dates: Range | null) {
  const anchors = new Map<number, Anchor>();
  const times = new Map<string, Range>();
  function anchor(time: number) {
    const existing = anchors.get(time);
    if (existing !== undefined) return existing;
    const added = { time, position: 0, width: 0, activeUntil: time };
    anchors.set(time, added);
    return added;
  }
  for (const entry of entries) {
    if (entry.kind === "user" && !entry.on_axis) continue;
    let [start, end] = interval(entry);
    if (dates) {
      start = clamp(start, dates);
      end = clamp(end, dates);
    }
    times.set(entry.id, [start, end]);
    const current = anchor(start);
    current.width = Math.max(current.width, widths[entry.kind]);
    current.activeUntil = Math.max(current.activeUntil, end);
    anchor(end);
  }
  const points = [...anchors.values()].sort((a, b) => a.time - b.time);
  if (points[0]) points[0].position = points[0].width / 2;
  const gaps: { plot: Range; time: Range }[] = [];
  let activeUntil = points[0]?.activeUntil ?? 0;
  // Give successive events space even when their wall-clock times nearly coincide.
  for (let index = 1; index < points.length; index++) {
    const before = points[index - 1];
    const current = points[index];
    const idle = activeUntil <= before.time && current.time - before.time > 60;
    const left = before.position + before.width / 2;
    const right = left + (idle ? 0.85 : 0.32);
    current.position = right + current.width / 2;
    if (idle) gaps.push({ plot: [left, right], time: [before.time, current.time] });
    activeUntil = Math.max(activeUntil, current.activeUntil);
  }
  function convert(value: number, source: "time" | "position", target: "time" | "position") {
    if (!points.length) return 0;
    let first = 0;
    let last = points.length - 1;
    while (last - first > 1) {
      const middle = Math.floor((first + last) / 2);
      if (points[middle][source] <= value) first = middle;
      else last = middle;
    }
    const before = points[first];
    const after = points[last];
    if (value <= before[source]) return before[target];
    if (value >= after[source]) return after[target];
    const portion = (value - before[source]) / (after[source] - before[source]);
    return before[target] + (after[target] - before[target]) * portion;
  }
  const toPlot = (time: number) => convert(time, "time", "position");
  const toTime = (position: number) => convert(position, "position", "time");
  const last = points.at(-1);
  const extent: Range = [0, Math.max(minimumView, (last?.position ?? 0) + (last?.width ?? 0) / 2)];
  const marks = entries.flatMap((entry): Mark[] => {
    const timespan = times.get(entry.id);
    if (!timespan) return [];
    const position = toPlot(timespan[0]);
    const width = Math.min(widths[entry.kind], extent[1]);
    const left = clamp(position - width / 2, [0, extent[1] - width]);
    return [{ entry, anchor: position, plot: [left, Math.max(left + width, toPlot(timespan[1]))] }];
  }).sort((a, b) => a.anchor - b.anchor);
  return { extent, marks, gaps, toPlot, toTime };
}

export type TraceAxis = ReturnType<typeof buildAxis>;

export function fitWindow(left: number, length: number, extent: Range): Range {
  const size = clamp(length, [Math.min(minimumView, span(extent)), span(extent)]);
  const start = clamp(left, [extent[0], extent[1] - size]);
  return [start, start + size];
}
