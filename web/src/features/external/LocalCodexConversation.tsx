import { ActionIcon, Alert, Button, Group, Loader, ScrollArea, Select, Stack, Text, Textarea } from "@mantine/core";
import type { ComboboxData, ComboboxItem } from "@mantine/core";
import { IconArrowUp, IconPlugConnected } from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import type { ExternalSessionSummary, NativeCodexProject } from "../../api/contracts";
import {
  apiErrorDetail, kondenApi, useGetLocalCodexChatQuery, useGetNativeCodexProjectsQuery, useGetWorkspacesQuery,
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

function draftKeys(projectId: string | null) {
  const suffix = projectId === null ? "" : `:project:${projectId}`;
  return { draftKey: NEW_DRAFT + suffix, pendingKey: PENDING_THREAD + suffix };
}

export function NewLocalCodexConversation() {
  const [params, setParams] = useSearchParams();
  const projectId = params.get("project");
  const [workspaceId, setWorkspaceId] = useState<string | null>(null);
  const projects = useGetNativeCodexProjectsQuery(undefined, { refetchOnMountOrArgChange: true });

  function onDirectoryChange(nextProject: string | null, nextWorkspace: string | null) {
    if (nextProject === null) setWorkspaceId(nextWorkspace);
    if (nextProject === projectId) return;
    const next = new URLSearchParams(params);
    if (nextProject === null) next.delete("project");
    else next.set("project", nextProject);
    setParams(next, { replace: true });
  }

  return <NewLocalCodexDraft key={draftKeys(projectId).draftKey} projectId={projectId} workspaceId={workspaceId}
    projectsQuery={projects} onDirectoryChange={onDirectoryChange} />;
}

function NewLocalCodexDraft({ projectId, workspaceId, projectsQuery, onDirectoryChange }: {
  projectId: string | null; workspaceId: string | null;
  projectsQuery: { currentData?: NativeCodexProject[] | null; isFetching: boolean; error?: unknown; refetch: () => unknown };
  onDirectoryChange: (projectId: string | null, workspaceId: string | null) => void;
}) {
  const navigate = useNavigate();
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const { draftKey, pendingKey } = draftKeys(projectId);
  const { data: workspaces = [] } = useGetWorkspacesQuery();
  const { currentData: projects, isFetching: readingProjects, error: projectError, refetch: refetchProjects } = projectsQuery;
  const project = projects?.find((item) => item.id === projectId);
  const [prepare, { isLoading: preparing }] = usePrepareLocalCodexMutation({ fixedCacheKey: `${draftKey}:prepare` });
  const [start, { isLoading: starting }] = useStartLocalCodexMutation({ fixedCacheKey: `${draftKey}:start` });
  const [draft, setDraft] = useState(() => restoreComposerText(draftKey));
  const [selectedRoot, setSelectedRoot] = useState<string | null>(null);
  const [pending, setPending] = useState(() => sessionStorage.getItem(pendingKey));
  const [failure, setFailure] = useState<string | null>(null);
  const roots = project?.roots.map((root) => root.path) ?? [];
  const root = selectedRoot !== null && roots.includes(selectedRoot) ? selectedRoot : roots.length === 1 ? roots[0] : null;
  const target = projectId === null
    ? { workspaceId }
    : root !== null && !projectError && !readingProjects ? { project: { id: projectId, root } } : null;
  const sending = preparing || starting;
  const active = useRef(false);
  useEffect(() => {
    active.current = true;
    return () => { active.current = false; };
  }, []);
  useEffect(() => { saveComposerText(draftKey, draft); }, [draftKey, draft]);

  const continuing = projectId === null && pending !== null;
  const directory = projectId !== null ? `codex:${projectId}` : workspaceId !== null && !continuing ? `konden:${workspaceId}` : "private";
  const projectOptions: ComboboxItem[] = (projects ?? []).filter((item) => item.roots.length > 0)
    .map((item) => ({ value: `codex:${item.id}`, label: item.name }));
  if (projectId !== null && !projectOptions.some((item) => item.value === directory)) {
    projectOptions.push({ value: directory, label: project?.name ?? "所选 Codex 工作区", disabled: true });
  }
  const directories: ComboboxData[number][] = [
    { value: "private", label: continuing ? "原会话的工作目录" : "新的独立目录", disabled: continuing },
  ];
  if (projectOptions.length > 0) directories.push({ group: "Codex 工作区", items: projectOptions });
  if (!continuing && workspaces.length > 0) {
    directories.push({ group: "Konden 工作目录", items: workspaces.map((item) => ({ value: `konden:${item.workspace_id}`, label: item.task_root })) });
  }

  function chooseDirectory(value: string | null) {
    if (value === null) return;
    const nextProject = value.startsWith("codex:") ? value.slice("codex:".length) : null;
    const nextWorkspace = value.startsWith("konden:") ? value.slice("konden:".length) : null;
    if (nextProject !== projectId && pending === null && draft !== "") {
      const next = draftKeys(nextProject);
      if (sessionStorage.getItem(next.pendingKey) === null && restoreComposerText(next.draftKey) === "") {
        saveComposerText(next.draftKey, draft);
        saveComposerText(draftKey, "");
      }
    }
    onDirectoryChange(nextProject, nextWorkspace);
  }

  async function submit() {
    if (connectionId === null || sending || !draft.trim()) return;
    setFailure(null);
    const text = draft;
    try {
      let identity = pending ?? sessionStorage.getItem(pendingKey);
      if (identity === null) {
        if (target === null) return;
        ({ identity } = await prepare({ connectionId, ...target }).unwrap());
        sessionStorage.setItem(pendingKey, identity);
        if (active.current) setPending(identity);
      }
      const chat = await start({ connectionId, identity, text }).unwrap();
      if (sessionStorage.getItem(pendingKey) === identity) {
        if (chat.messages.some((message) => message.role === "user" && message.text === text)) {
          clearSentComposerText(draftKey, text);
        }
        sessionStorage.removeItem(pendingKey);
      }
      if (active.current) navigate(`/external/${encodeURIComponent(identity)}`, { replace: true });
    } catch (cause) {
      if (active.current) setFailure(typeof cause === "string" ? cause : "未确认发送结果。重试时会检查原会话。");
    }
  }

  return <div className="session-page external-session-page">
    <div className="session-main">
      <header className="page-header session-header local-codex-header local-codex-draft-header">
        <Group gap={10} wrap="nowrap" className="session-header-identity">
          <span className="agent-avatar agent-avatar-small" aria-hidden="true"><IconPlugConnected size={18} /></span>
          <Text size="sm" fw={600}>新建 Codex 会话</Text>
        </Group>
        <Stack gap={5} w={230} maw="48%">
          <Select aria-label="工作目录" size="xs" data={directories} value={directory} allowDeselect={false}
            title={pending === null ? root ?? undefined : undefined} disabled={sending} onChange={chooseDirectory}
            rightSection={readingProjects ? <Loader size="xs" aria-label="正在读取 Codex 工作区" /> : undefined} />
          {pending === null && roots.length > 1 ? <Select aria-label="工作区根目录" size="xs"
            data={roots} value={root} placeholder="选择工作区根目录" title={root ?? undefined}
            allowDeselect={false} disabled={sending || readingProjects} onChange={setSelectedRoot} /> : null}
        </Stack>
      </header>
      {projectError ? <Alert color="orange" role="alert">
        {typeof projectError === "string" ? projectError : "无法读取 Codex 工作区。"}
        <Button size="compact-xs" variant="subtle" onClick={() => void refetchProjects()}>重试</Button>
      </Alert> : null}
      {pending === null && projectId !== null && roots.length === 0 && !readingProjects && !projectError
        ? <Alert color="orange" role="alert">所选 Codex 工作区没有可用的根目录，请重新选择。</Alert> : null}
      <div className="group-chat-scroll">
        <div className="group-chat-empty"><Text fw={500} size="lg">给 Codex 发条消息</Text></div>
      </div>
      {sending ? <Text ta="center" role="status" c="dimmed" size="sm">正在等待 Codex 回复…</Text> : null}
      {failure === null ? null : <Alert color="orange" role="alert">{failure}</Alert>}
      <CodexComposer draft={draft} onChange={setDraft} onSend={submit} sending={sending} readOnly={sending}
        disabled={connectionId === null || (pending === null && target === null)} />
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
