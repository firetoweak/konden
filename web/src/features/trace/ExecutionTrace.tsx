import { useMemo, useRef, useState, type CSSProperties, type FormEvent } from "react";
import { IconChevronLeft } from "@tabler/icons-react";
import { Virtuoso, type VirtuosoHandle } from "react-virtuoso";
import { useGetExecutionTraceQuery } from "../../api/kondenApi";
import type { TraceEntry, TraceIndex, TraceScope } from "../../api/traceContracts";
import { TraceChart, toolMatches, type TraceStatusFilter } from "./TraceChart";
import { CallStatus, TraceDetail } from "./TraceDetail";
import { buildAxis, clamp, dated, elapsed, interval, overlaps, type Range } from "./traceAxis";
import "./trace.css";

const roles: Record<TraceEntry["kind"], string> = {
  user: "用户", input: "输入", model: "助手", tool: "工具", catalog: "目录", compact: "Compact", subagent: "Subagent",
};
const filters = [["all", "调用"], ["failed", "失败"], ["unknown", "结果未知"]] as const;

function RecordRow({ entry, selected, open }: { entry: TraceEntry; selected: boolean; open: () => void }) {
  const tool = entry.kind === "tool";
  const text = tool ? entry.preview : entry.kind === "catalog" || entry.kind === "compact"
    ? [entry.title, entry.preview].filter(Boolean).join(" · ") : entry.preview || "无文本内容";
  return <button type="button" className={`trace-record ${entry.kind}`} aria-pressed={selected} onClick={open}
    aria-label={`${roles[entry.kind]} · ${entry.kind === "model" || tool ? entry.title : text} · ${dated(entry.at)}`}>
    <span className="trace-record-role">{roles[entry.kind]}</span>
    <span className={`trace-record-content${tool ? " call" : ""}`}>
      <span className="trace-record-preview">{tool && <code>{entry.title}</code>}{text && <span>{text}</span>}</span>
      {entry.result && <span className="trace-record-result"><span aria-hidden="true">→</span><span>{entry.result}</span></span>}
    </span>
    <span className="trace-record-meta">
      {tool ? <><CallStatus entry={entry} />{entry.end != null && <span className="trace-record-duration">{elapsed(interval(entry))}</span>}</>
        : entry.kind === "model" && <span className="trace-record-step">{entry.title}</span>}
      <time dateTime={new Date(entry.at * 1000).toISOString()} title={dated(entry.at, false, true)}>{dated(entry.at)}</time>
    </span>
  </button>;
}

function DateFilter({ apply }: { apply: (range: Range | null) => void }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [active, setActive] = useState(false);
  const [invalid, setInvalid] = useState(false);
  function filter(event: FormEvent) {
    event.preventDefault();
    const error = Boolean(from && to && from > to);
    setInvalid(error);
    if (error) return;
    const start = from ? Date.parse(`${from}T00:00:00+08:00`) / 1000 : -Infinity;
    const end = to ? Date.parse(`${to}T00:00:00+08:00`) / 1000 + 86400 - 0.000001 : Infinity;
    const filtered = Boolean(from || to);
    setActive(filtered);
    apply(filtered ? [start, end] : null);
  }
  return <form className="trace-date-filter" aria-label="日期检索" onSubmit={filter}>
    <div className="trace-date-controls">
      <input type="date" value={from} aria-label="开始日期" onChange={(event) => { setFrom(event.target.value); setInvalid(false); }} />
      <span>至</span><input type="date" value={to} aria-label="结束日期" aria-invalid={invalid}
        onChange={(event) => { setTo(event.target.value); setInvalid(false); }} />
      <button type="submit">检索</button>
      {active && <button type="button" className="trace-link" onClick={() => {
        setFrom(""); setTo(""); setInvalid(false); setActive(false); apply(null);
      }}>清除日期</button>}
    </div>
    {invalid && <span className="trace-date-error" role="alert">结束日期早于开始日期</span>}
  </form>;
}

function TraceWorkspace({ index, scope, openBranch, back }: {
  index: TraceIndex; scope: TraceScope; openBranch: (command: string) => void; back: () => void;
}) {
  const [dates, setDates] = useState<Range | null>(null);
  const [viewport, setViewport] = useState<Range | null>(null);
  const [range, setRange] = useState<Range | null>(null);
  const [status, setStatus] = useState<TraceStatusFilter>("all");
  const [tool, setTool] = useState("");
  const [selection, select] = useState<string>();
  const [share, setShare] = useState(50);
  const records = useRef<VirtuosoHandle>(null);
  const pane = useRef<HTMLDivElement>(null);
  const resizing = useRef<number | null>(null);
  const { entries } = index;
  const inDates = useMemo(() => dates ? entries.filter((entry) => overlaps(interval(entry), dates)) : entries, [dates, entries]);
  const axis = useMemo(() => buildAxis(inDates, dates), [inDates, dates]);
  const rangeEntries = useMemo(() => range ? inDates.filter((entry) => overlaps(interval(entry), range)) : inDates, [inDates, range]);
  const calls = useMemo(() => inDates.filter((entry) => entry.kind === "tool"), [inDates]);
  const names = useMemo(() => [...new Set(calls.map((entry) => entry.title))].sort(), [calls]);
  const counts = useMemo(() => ({ all: calls.length, failed: calls.filter((entry) => entry.status === "failed").length,
    unknown: calls.filter((entry) => entry.status === "unknown").length }), [calls]);
  const summary = useMemo(() => {
    if (!inDates.length) return null;
    let first = Infinity;
    let last = 0;
    let users = 0;
    let models = 0;
    for (const entry of inDates) {
      const [start, end] = interval(entry);
      first = Math.min(first, start);
      last = Math.max(last, entry.at, end);
      users += Number(entry.kind === "user");
      models += Number(entry.kind === "model");
    }
    return { first: dates ? clamp(first, dates) : first, last: dates ? clamp(last, dates) : last, users, models };
  }, [inDates, dates]);
  const filtered = tool !== "" || status !== "all";
  const { rows, median } = useMemo(() => {
    const visibleCalls = rangeEntries.filter((entry) => entry.kind === "tool" && toolMatches(entry, tool, status));
    const visibleSteps = new Set(visibleCalls.map((entry) => entry.step));
    const rows = rangeEntries.filter((entry) => {
      if (entry.kind === "input" || entry.kind === "subagent") return false;
      if (entry.kind === "tool") return toolMatches(entry, tool, status);
      return !filtered || (entry.kind === "model" && visibleSteps.has(entry.step));
    });
    const durations = tool === "" ? [] : visibleCalls.flatMap((entry) => entry.end == null ? [] : [Math.max(0, entry.end - entry.start)]).sort((a, b) => a - b);
    const middle = Math.floor(durations.length / 2);
    const median = durations.length === 0 ? null : (durations.length % 2 ? durations[middle] : (durations[middle - 1] + durations[middle]) / 2);
    return { rows, median };
  }, [rangeEntries, tool, status, filtered]);
  const detail = selection === undefined ? undefined : rangeEntries.find((entry) => entry.id === selection);

  function applyDates(next: Range | null) {
    setDates(next); setViewport(null); setRange(null); setTool(""); select(undefined);
    records.current?.scrollToIndex({ index: 0 });
  }

  function choose(entry: TraceEntry, fromChart: boolean) {
    if (entry.branch !== undefined) { openBranch(entry.branch); return; }
    select(entry.id);
    if (fromChart) {
      const id = entry.kind === "input" ? `model:${entry.sequence}` : entry.id;
      const index = rows.findIndex((row) => row.id === id);
      if (index >= 0) records.current?.scrollIntoView({ index });
    }
  }

  return <div className="trace-workspace">
    <div className="trace-top">
      <div className="trace-summary-bar">
        <div className="trace-summary">
          {scope.branch.length > 0 && <button type="button" className="trace-link trace-back" onClick={back}><IconChevronLeft size={14} />返回上级轨迹</button>}
          {summary && <><span>{dated(summary.first, true)} – {dated(summary.last, true)}</span>
            <span>{summary.users} 条用户消息 · {summary.models} 次模型决策</span></>}
        </div>
        <DateFilter apply={applyDates} />
        <div className="trace-counts" role="group" aria-label="调用状态筛选">
          {filters.map(([value, label]) => <button key={value} type="button" aria-pressed={value === status}
            onClick={() => { setStatus(value); records.current?.scrollToIndex({ index: 0 }); }}>
            {value === status && <span aria-hidden="true">✓</span>} {label} <strong>{counts[value]}</strong></button>)}
        </div>
      </div>
      {index.task && <p className="trace-task" title={index.task}>{index.task}</p>}
      <TraceChart key={JSON.stringify(dates)} axis={axis} window={viewport} setWindow={setViewport} range={range} setRange={(next) => {
        setRange(next); records.current?.scrollToIndex({ index: 0 });
      }} selected={detail?.id} onSelect={(entry) => choose(entry, true)} status={status} tool={tool} />
    </div>
    <div className={`trace-panes${detail ? " has-detail" : ""}`} ref={pane} style={{ "--trace-share": `${share}%` } as CSSProperties}>
      <section className="trace-records-pane" aria-label="执行记录">
        <header className="trace-pane-heading trace-records-heading"><strong>执行记录</strong>
          <span className="trace-record-count">{rows.length} 条记录{median !== null && ` · 中位耗时 ${elapsed([0, median])}`}</span>
          <select aria-label="按工具筛选" value={tool} onChange={(event) => { setTool(event.target.value); records.current?.scrollToIndex({ index: 0 }); }}>
            <option value="">全部工具</option>{names.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
          {(filtered || range) && <button type="button" className="trace-link" onClick={() => { setStatus("all"); setTool(""); setRange(null); }}>清除筛选</button>}
        </header>
        {rows.length > 0 ? <Virtuoso className="trace-record-list" ref={records} role="region" aria-label="执行记录列表"
          data={rows} fixedItemHeight={34} computeItemKey={(_, entry) => entry.id}
          itemContent={(_, entry) => <RecordRow entry={entry} selected={entry.id === detail?.id} open={() => choose(entry, false)} />} />
          : <p className="trace-empty" role="status">{entries.length === 0 ? "暂无执行轨迹" : "当前筛选范围内没有执行记录"}</p>}
      </section>
      {detail && <><div className="trace-divider" role="separator" aria-label="调整执行记录和详情宽度" aria-orientation="vertical"
        aria-valuemin={25} aria-valuemax={75} aria-valuenow={Math.round(share)} tabIndex={0}
        onPointerDown={(event) => {
          if (event.button !== 0) return;
          resizing.current = event.pointerId; event.currentTarget.setPointerCapture(event.pointerId);
        }} onPointerMove={(event) => {
          if (resizing.current !== event.pointerId) return;
          const box = pane.current!.getBoundingClientRect();
          setShare(clamp((event.clientX - box.left) / box.width * 100, [25, 75]));
        }} onPointerUp={() => { resizing.current = null; }} onPointerCancel={() => { resizing.current = null; }}
        onKeyDown={(event) => {
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault(); setShare((current) => clamp(current + (event.key === "ArrowLeft" ? -5 : 5), [25, 75]));
          }
        }} />
        <TraceDetail key={detail.id} entry={detail} scope={scope} observedSession={index.session_id} close={() => select(undefined)} />
      </>}
    </div>
  </div>;
}

export function ExecutionTrace({ sessionId }: { sessionId: string }) {
  const [branch, setBranch] = useState<string[]>([]);
  const scope = { sessionId, branch };
  const { currentData, error, refetch } = useGetExecutionTraceQuery(scope, { refetchOnMountOrArgChange: true });
  const back = () => setBranch((current) => current.slice(0, -1));
  if (currentData === undefined) return <div className="trace-workspace trace-state">
    {branch.length > 0 && <button type="button" className="trace-link trace-back" onClick={back}><IconChevronLeft size={14} />返回上级轨迹</button>}
    {error ? <p role="alert">读取执行轨迹失败 <button type="button" className="trace-link" onClick={refetch}>重试</button></p>
      : <p role="status">读取执行轨迹…</p>}
  </div>;
  return <TraceWorkspace key={JSON.stringify(branch)} index={currentData} scope={scope}
    openBranch={(command) => setBranch((current) => [...current, command])} back={back} />;
}
