import { ActionIcon, Alert, Button, Group, Loader, ScrollArea, Select, Text, Textarea } from "@mantine/core";
import { IconArrowUp, IconPlugConnected } from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import type { ExternalSessionSummary } from "../../api/contracts";
import {
  apiErrorDetail, kondenApi, useGetLocalCodexChatQuery, useGetWorkspacesQuery,
  usePrepareLocalCodexMutation, useSendLocalCodexMutation, useStartLocalCodexMutation,
} from "../../api/kondenApi";
import { useAppDispatch, useAppSelector } from "../../app/hooks";
import { MarkdownMessage } from "../conversation/MarkdownMessage";
import { clearSentComposerText, restoreComposerText, saveComposerText } from "../conversation/composerDraft";
import { useMessageReading } from "../conversation/useMessageReading";
import { CodexDesktopLink } from "./CodexDesktopLink";

const NEW_DRAFT = "local-codex:new";
const PENDING_THREAD = "konden.codexFirstMessage.thread";
const speakers = { user: "你的消息", codex: "Codex 的回复", delegation: "Konden Bot 的派单" };

export function NewLocalCodexConversation() {
  const navigate = useNavigate();
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const { data: workspaces = [] } = useGetWorkspacesQuery();
  const [prepare, { isLoading: preparing }] = usePrepareLocalCodexMutation({ fixedCacheKey: `${NEW_DRAFT}:prepare` });
  const [start, { isLoading: starting }] = useStartLocalCodexMutation({ fixedCacheKey: `${NEW_DRAFT}:start` });
  const [draft, setDraft] = useState(() => restoreComposerText(NEW_DRAFT));
  const [workspace, setWorkspace] = useState("private");
  const [pending, setPending] = useState(() => sessionStorage.getItem(PENDING_THREAD));
  const [failure, setFailure] = useState<string | null>(null);
  const sending = preparing || starting;
  const active = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  useEffect(() => { saveComposerText(NEW_DRAFT, draft); }, [draft]);

  async function submit() {
    if (connectionId === null || sending || !draft.trim()) return;
    setFailure(null);
    const text = draft;
    try {
      let identity = pending ?? sessionStorage.getItem(PENDING_THREAD);
      if (identity === null) {
        ({ identity } = await prepare({ connectionId, workspaceId: workspace === "private" ? null : workspace }).unwrap());
        sessionStorage.setItem(PENDING_THREAD, identity);
        if (active.current) setPending(identity);
      }
      const chat = await start({ connectionId, identity, text }).unwrap();
      if (sessionStorage.getItem(PENDING_THREAD) === identity) {
        if (chat.messages.some((message) => message.role === "user" && message.text === text)) {
          clearSentComposerText(NEW_DRAFT, text);
        }
        sessionStorage.removeItem(PENDING_THREAD);
      }
      if (active.current) navigate(`/external/${encodeURIComponent(identity)}`, { replace: true });
    } catch (cause) {
      if (active.current) setFailure(typeof cause === "string" ? cause : "未确认发送结果。重试时会检查原会话。");
    }
  }

  return <div className="session-page external-session-page">
    <div className="session-main">
      <header className="page-header session-header local-codex-header">
        <Group gap={10} wrap="nowrap" className="session-header-identity">
          <span className="agent-avatar agent-avatar-small" aria-hidden="true"><IconPlugConnected size={18} /></span>
          <Text size="sm" fw={600}>新建 Codex 会话</Text>
        </Group>
        {pending === null ? <Select aria-label="工作目录" size="xs" w={230} maw="48%"
          data={[{ value: "private", label: "新的独立目录" }, ...workspaces.map((item) => ({ value: item.workspace_id, label: item.task_root }))]}
          value={workspace} allowDeselect={false} disabled={sending}
          onChange={(value) => { if (value !== null) setWorkspace(value); }} /> : null}
      </header>
      <div className="group-chat-scroll">
        <div className="group-chat-empty"><Text fw={500} size="lg">给 Codex 发条消息</Text></div>
      </div>
      {sending ? <Text ta="center" role="status" c="dimmed" size="sm">正在等待 Codex 回复…</Text> : null}
      {failure === null ? null : <Alert color="orange" role="alert">{failure}</Alert>}
      <CodexComposer draft={draft} onChange={setDraft} onSend={submit} sending={sending} readOnly={sending}
        disabled={connectionId === null} />
    </div>
  </div>;
}

export function LocalCodexConversation({ session }: { session: ExternalSessionSummary }) {
  const identity = session.identity;
  const draftKey = `local-codex:${identity}`;
  const dispatch = useAppDispatch();
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const { data, error, refetch, isFetching } = useGetLocalCodexChatQuery(identity);
  const [send, { isLoading: sending }] = useSendLocalCodexMutation();
  const [draft, setDraft] = useState(() => restoreComposerText(draftKey));
  const [failure, setFailure] = useState<string | null>(null);
  const readingMessages = useMemo(() => data?.messages.map(({ id }) => ({ message_id: id })), [data?.messages]);
  const reading = useMessageReading(draftKey, readingMessages);
  useEffect(() => { saveComposerText(draftKey, draft); }, [draftKey, draft]);

  async function submit() {
    if (connectionId === null || !data || !draft.trim() || sending || isFetching) return;
    const text = draft;
    setFailure(null);
    try {
      const updated = await send({ connectionId, identity, text }).unwrap();
      dispatch(kondenApi.util.updateQueryData("getLocalCodexChat", identity, () => updated));
      clearSentComposerText(draftKey, text);
      setDraft((current) => current === text ? "" : current);
    } catch (cause) {
      setFailure(typeof cause === "string" ? cause : "未收到 Codex 的确认。请刷新查看后再决定是否重发。");
    }
  }

  const readFailure = error === undefined ? null : ("data" in error ? apiErrorDetail(error.data) : undefined)
    ?? "无法读取 Codex 会话。";
  return <div className="session-page external-session-page">
    <div className="session-main">
      <header className="page-header session-header local-codex-header">
        <Group gap={10} wrap="nowrap" className="session-header-identity">
          <span className="agent-avatar agent-avatar-small" aria-hidden="true"><IconPlugConnected size={18} /></span>
          <div className="session-header-name">
            <Text size="sm" fw={600} truncate>{session.name}</Text>
            {data ? <Text c="dimmed" fz={11} truncate title={data.workspace}>{data.workspace}</Text> : null}
          </div>
        </Group>
        <Group gap={4} wrap="nowrap">
          {session.active ? <Button component={Link} to="?view=shared" variant="subtle" size="compact-xs">三方对话</Button> : null}
          <Button variant="subtle" size="compact-xs" loading={isFetching} disabled={sending} onClick={() => void refetch()}>刷新</Button>
          {data ? <CodexDesktopLink threadId={data.thread_id} /> : null}
        </Group>
      </header>
      {readFailure !== null ? <Alert color="orange" role="alert">{readFailure}</Alert> : null}
      <ScrollArea className="group-chat-scroll" type="hover" viewportRef={reading.viewport}
        onScrollPositionChange={({ y }) => reading.onScroll(y)}>
        <div className="group-transcript" role="region" aria-label="Codex 会话消息">
          {!data && !readFailure ? <div className="conversation-loading"><Loader size="sm" /> 正在打开 Codex 会话…</div> : null}
          {data?.has_earlier ? <Text c="dimmed" size="xs" ta="center">这里显示最近 20 轮。更早的消息可在 Desktop 查看。</Text> : null}
          {data?.messages.length === 0 ? <div className="group-chat-empty">
            <Text fw={500} size="lg">给 Codex 发条消息</Text>
          </div> : null}
          {data?.messages.map((message) => <div className="group-message-slot" key={message.id}>
            <article data-message-id={message.id} className={message.role === "user" ? "group-message is-human" : "group-message"}
              aria-label={speakers[message.role]}>
              <div className="message group-message-row">
                {message.role === "user" ? <span className="agent-avatar human-avatar" aria-hidden="true">你</span>
                  : <span className="agent-avatar" aria-hidden="true"><IconPlugConnected size={18} /></span>}
                <div className="group-message-bubble">
                  {message.role === "delegation" ? <Text size="xs" fw={500} c="dimmed" mb={6}>Konden Bot · A2A 派单</Text> : null}
                  <MarkdownMessage content={message.text} streaming={false} />
                </div>
              </div>
            </article>
          </div>)}
          {sending || data?.last_status === "inProgress" ? <Text role="status" c="dimmed" size="sm">正在等待 Codex 回复…</Text> : null}
          {!sending && (data?.last_status === "failed" || data?.last_status === "interrupted")
            ? <Alert color="orange">Codex 这一轮未完成。</Alert> : null}
        </div>
      </ScrollArea>
      {failure === null ? null : <Alert color="orange" role="alert">{failure}</Alert>}
      <CodexComposer draft={draft} onChange={setDraft} onSend={submit} sending={sending}
        disabled={connectionId === null || !data || isFetching} />
    </div>
  </div>;
}

function CodexComposer({ draft, onChange, onSend, sending, disabled, readOnly = false }: {
  draft: string; onChange: (text: string) => void; onSend: () => Promise<void>;
  sending: boolean; disabled: boolean; readOnly?: boolean;
}) {
  return <form className="composer local-codex-composer" onSubmit={(event) => { event.preventDefault(); void onSend(); }}>
    <Group align="flex-end" wrap="nowrap" gap={8}>
      <Textarea aria-label="发给 Codex 的消息" placeholder="发消息给 Codex" autosize minRows={1} maxRows={6}
        className="local-codex-input" value={draft} readOnly={readOnly} onChange={(event) => onChange(event.currentTarget.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
            event.preventDefault();
            void onSend();
          }
        }} />
      <ActionIcon type="submit" aria-label="发送给 Codex" size="lg" loading={sending}
        disabled={disabled || !draft.trim()}><IconArrowUp size={17} /></ActionIcon>
    </Group>
  </form>;
}
