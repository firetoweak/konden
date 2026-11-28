import { useState, type ReactNode } from "react";
import { IconX } from "@tabler/icons-react";
import { useGetExecutionTraceDetailQuery } from "../../api/kondenApi";
import { traceParams, type TraceDetail as Detail, type TraceEntry, type TraceMessage, type TraceScope } from "../../api/traceContracts";
import { AttachmentTile, attachmentUrl } from "../conversation/AttachmentTile";
import { statusLabel } from "./TraceChart";
import { cacheLabel, dated, elapsed, interval } from "./traceAxis";

const headings: Record<TraceEntry["kind"], string> = {
  input: "输入详情", model: "模型详情", tool: "调用详情", user: "用户消息", catalog: "工具目录详情", compact: "Compact 详情", subagent: "子 Agent",
};

function Code({ value }: { value: unknown }) {
  return <pre>{typeof value === "string" ? value : JSON.stringify(value, null, 2)}</pre>;
}

function Disclosure({ title, count, initiallyOpen = false, children }: {
  title: ReactNode; count?: string; initiallyOpen?: boolean; children: ReactNode;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  return <details className="trace-disclosure" open={open} onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>{title}{count && <span>{count}</span>}</summary>
    {open && <div>{children}</div>}
  </details>;
}

function PromptImage({ id, name, sessionId, branch }: TraceScope & { id: string; name: string }) {
  const params = traceParams(branch);
  const url = attachmentUrl(sessionId, id) + (params.size ? `?${params}` : "");
  return <figure className="trace-image"><AttachmentTile src={url} name={name} downloadHref={url} large /><figcaption>{name}</figcaption></figure>;
}

function PromptBlock({ value, scope }: { value: unknown; scope: TraceScope }) {
  if (typeof value === "object" && value !== null && "type" in value) {
    if (value.type === "text" && "text" in value) return <Code value={value.text} />;
    if (value.type === "image" && "id" in value && typeof value.id === "string" && /^sha256:[0-9a-f]{64}$/.test(value.id)) {
      const size = "width" in value && "height" in value ? ` ${value.width} × ${value.height}` : "";
      return <PromptImage {...scope} id={value.id} name={`输入图片${size}`} />;
    }
  }
  return <Code value={value} />;
}

function PromptMessage({ message, index, scope, initiallyOpen }: {
  message: TraceMessage; index: number; scope: TraceScope; initiallyOpen?: boolean;
}) {
  const { role, content, ...fields } = message;
  return <Disclosure title={<code>{role}</code>} count={`第 ${index + 1} 条`} initiallyOpen={initiallyOpen}>
    {Array.isArray(content) ? content.map((block, part) => <PromptBlock key={part} value={block} scope={scope} />)
      : content != null && <Code value={content} />}
    {Object.keys(fields).length > 0 && <Disclosure title="消息字段"><Code value={fields} /></Disclosure>}
  </Disclosure>;
}

function PromptHistory({ messages, start, end, scope }: { messages: TraceMessage[]; start: number; end: number; scope: TraceScope }) {
  const [limit, setLimit] = useState(60);
  return <>
    {messages.slice(start, Math.min(end, start + limit)).map((message, index) =>
      <PromptMessage key={start + index} message={message} index={start + index} scope={scope} />)}
    {start + limit < end && <button type="button" className="trace-link" onClick={() => setLimit(limit + 60)}>展开更多历史消息</button>}
  </>;
}

function ModelInput({ detail, scope }: { detail: Extract<Detail, { kind: "input" }>; scope: TraceScope }) {
  const request = detail.request;
  if (request === null) return <>
    <p>此步没有模型请求存档</p>
    {detail.triggers.map(({ sequence, kind, text }) => <Disclosure key={sequence} title={text === null ? kind : <code>user</code>} count={`Journal #${sequence}`}>
      {text !== null && <Code value={text} />}
    </Disclosure>)}
  </>;
  const { model, messages, tools } = request;
  let head = 0;
  while (head < messages.length && ["system", "developer"].includes(messages[head].role)) head++;
  const tail = Math.max(head, messages.length - 6);
  return <>
    <h4>模型请求 <small>{model} · {messages.length} 条消息</small></h4>
    {messages.slice(0, head).map((message, index) => <PromptMessage key={index} message={message} index={index} scope={scope} />)}
    {head < tail && <Disclosure title="历史消息" count={`${tail - head} 条`}>
      <PromptHistory messages={messages} start={head} end={tail} scope={scope} />
    </Disclosure>}
    {messages.slice(tail).map((message, index) => <PromptMessage key={tail + index} message={message} index={tail + index} scope={scope}
      initiallyOpen={tail + index === messages.length - 1} />)}
    {tools && tools.length > 0 && <Disclosure title="工具定义" count={`${tools.length} 项`}><Code value={tools} /></Disclosure>}
  </>;
}

function ToolDetail({ detail, entry }: { detail: Extract<Detail, { kind: "tool" }>; entry: TraceEntry }) {
  const [tab, selectTab] = useState<"arguments" | "result">("arguments");
  return <>
    <p className="trace-tool-meta"><CallStatus entry={entry} />{entry.end != null && elapsed(interval(entry))}</p>
    <div className="trace-detail-nav" role="group" aria-label="调用内容">
      <button type="button" aria-pressed={tab === "arguments"} onClick={() => selectTab("arguments")}>参数</button>
      <button type="button" aria-pressed={tab === "result"} onClick={() => selectTab("result")}>结果</button>
    </div>
    {tab === "arguments" ? <Code value={detail.arguments} /> : <>
      {detail.outcome === null ? <p>{entry.status ? statusLabel[entry.status] : "结果未知"}</p> : <Code value={detail.outcome} />}
      <dl className="trace-detail-times">
        <div><dt>提交调用</dt><dd>{dated(detail.issued_at, false, true)}</dd></div>
        {detail.dispatched_at !== null && <div><dt>派发</dt><dd>{dated(detail.dispatched_at, false, true)}</dd></div>}
        {detail.received_at !== null && <div><dt>记录返回</dt><dd>{dated(detail.received_at, false, true)}</dd></div>}
      </dl>
      {detail.attempts.length > 1 && <Disclosure title="派发记录" count={`${detail.attempts.length} 次`}>
        <dl className="trace-detail-times">{detail.attempts.map(({ number, at, phase }) =>
          <div key={number}><dt>第 {number} 次 · {phase === "orphaned" ? "中断" : phase === "terminal" ? "已记录结果" : "结果未知"}</dt>
            <dd>{dated(at, false, true)}</dd></div>)}</dl>
      </Disclosure>}
    </>}
  </>;
}

function toolName(tool: unknown) {
  if (typeof tool !== "object" || tool === null || !("function" in tool)) return null;
  const fn = tool.function;
  return typeof fn === "object" && fn !== null && "name" in fn && typeof fn.name === "string" ? fn.name : null;
}

function DetailContent({ detail, entry, scope }: { detail: Detail; entry: TraceEntry; scope: TraceScope }) {
  switch (detail.kind) {
    case "input": return <ModelInput detail={detail} scope={scope} />;
    case "tool": return <ToolDetail detail={detail} entry={entry} />;
    case "user": return <><Code value={detail.text} />{detail.images.map((id) => <PromptImage key={id} {...scope} id={id} name="用户附件" />)}</>;
    case "model": return <>
      <p className="trace-detail-meta">{entry.usage?.input_tokens !== undefined && `输入 ${entry.usage.input_tokens} tokens · `}
        {entry.usage?.output_tokens !== undefined && `输出 ${entry.usage.output_tokens} tokens · `}
        {entry.usage?.cached_input_tokens !== undefined && `缓存命中 ${entry.usage.cached_input_tokens.toLocaleString("zh-CN")} tokens · `}
        {cacheLabel(entry)}
        {entry.usage?.cache_write_input_tokens !== undefined && ` · 缓存写入 ${entry.usage.cache_write_input_tokens.toLocaleString("zh-CN")} tokens`}</p>
      {detail.reasoning && <Disclosure title="思考" initiallyOpen><Code value={detail.reasoning} /></Disclosure>}
      <h4>{detail.output_source === "recorded" ? "模型输出" : "已提交的决策"}</h4>
      <Code value={detail.output} />
    </>;
    case "catalog": return <>
      <h4>登记的工具集 · {detail.toolsets.length} 个</h4><Code value={detail.toolsets.map((item) => item.id).join("\n")} />
      {detail.tools.length > 0 && <><h4>首轮模型收到的工具 · {detail.tools.length} 个</h4>
        <Code value={detail.tools.map(toolName).filter(Boolean).join("\n")} />
        <Disclosure title="工具定义"><Code value={detail.tools} /></Disclosure>
      </>}
    </>;
    case "compact": return <>
      <p className="trace-detail-meta">{detail.published ? "已切换窗口" : "摘要已归档"}
        {detail.started_at !== null && ` · ${elapsed([detail.started_at, detail.at])}`}</p>
      <dl className="trace-detail-times">
        {detail.started_at !== null && <div><dt>任务登记</dt><dd>{dated(detail.started_at, false, true)}</dd></div>}
        <div><dt>{detail.published ? "窗口切换" : "摘要归档"}</dt><dd>{dated(detail.at, false, true)}</dd></div>
        <div><dt>整理至</dt><dd>Journal #{detail.upto}</dd></div>
      </dl>
      {detail.summary === null ? <p>无摘要存档</p> : <><h4>Compact 摘要</h4><Code value={detail.summary} /></>}
    </>;
  }
}

export function CallStatus({ entry }: { entry: TraceEntry }) {
  if (!entry.status) return null;
  const symbol = entry.status === "passed" ? "✓" : entry.status === "failed" ? "×" : "·";
  return <span className={`trace-status-chip ${entry.status}`}>{symbol} {statusLabel[entry.status]}</span>;
}

export function TraceDetail({ entry, scope, observedSession, close }: {
  entry: TraceEntry; scope: TraceScope; observedSession: string; close: () => void;
}) {
  const { currentData, error, refetch } = useGetExecutionTraceDetailQuery({ ...scope, observedSession, entry: entry.id });
  return <section className="trace-detail-pane" aria-label={headings[entry.kind]}>
    <header className="trace-pane-heading"><strong>{headings[entry.kind]}</strong>
      <button type="button" className="trace-close" aria-label="关闭详情" onClick={close}><IconX size={15} /></button></header>
    <div className="trace-detail-content" tabIndex={0} role="region" aria-label="详情内容">
      <h3>{entry.kind === "tool" ? <code>{entry.title}</code> : entry.title}</h3>
      <p className="trace-detail-meta">{entry.kind === "tool" && `第 ${entry.step} 步 · `}
        {dated(entry.start, false, true)} · Journal #{entry.sequence}</p>
      {currentData === undefined ? error ? <p role="alert">读取轨迹详情失败 <button type="button" className="trace-link" onClick={refetch}>重试</button></p>
        : <p role="status">读取轨迹详情…</p> : <DetailContent detail={currentData} entry={entry} scope={scope} />}
    </div>
  </section>;
}
