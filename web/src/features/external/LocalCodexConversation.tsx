import { ActionIcon, Alert, Button, Group, Loader, ScrollArea, Text, Textarea } from "@mantine/core";
import { IconArrowUp, IconPlugConnected } from "@tabler/icons-react";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import type { ExternalSessionSummary } from "../../api/contracts";
import { apiErrorDetail, kondenApi, useGetLocalCodexChatQuery, useSendLocalCodexMutation } from "../../api/kondenApi";
import { useAppDispatch, useAppSelector } from "../../app/hooks";
import { MarkdownMessage } from "../conversation/MarkdownMessage";
import { clearSentComposerText, restoreComposerText, saveComposerText } from "../conversation/composerDraft";
import { useMessageReading } from "../conversation/useMessageReading";
import { CodexDesktopLink } from "./CodexDesktopLink";

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
              aria-label={message.role === "user" ? "你的消息" : "Codex 的回复"}>
              <div className="message group-message-row">
                {message.role === "user" ? <span className="agent-avatar human-avatar" aria-hidden="true">你</span>
                  : <span className="agent-avatar" aria-hidden="true"><IconPlugConnected size={18} /></span>}
                <div className="group-message-bubble"><MarkdownMessage content={message.text} streaming={false} /></div>
              </div>
            </article>
          </div>)}
          {sending ? <Text role="status" c="dimmed" size="sm">正在等待 Codex 回复…</Text> : null}
          {!sending && (data?.last_status === "failed" || data?.last_status === "interrupted")
            ? <Alert color="orange">Codex 这一轮未完成。</Alert> : null}
        </div>
      </ScrollArea>
      {failure === null ? null : <Alert color="orange" role="alert">{failure}</Alert>}
      <form className="composer local-codex-composer" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
        <Group align="flex-end" wrap="nowrap" gap={8}>
          <Textarea aria-label="发给 Codex 的消息" placeholder="发消息给 Codex" autosize minRows={1} maxRows={6}
            className="local-codex-input" value={draft} onChange={(event) => setDraft(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                event.preventDefault();
                void submit();
              }
            }} />
          <ActionIcon type="submit" aria-label="发送给 Codex" size="lg" loading={sending}
            disabled={connectionId === null || !data || !draft.trim() || isFetching}><IconArrowUp size={17} /></ActionIcon>
        </Group>
      </form>
    </div>
  </div>;
}
