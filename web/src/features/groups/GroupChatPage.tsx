import {
  ActionIcon, Alert, Button, Drawer, Group, Loader, ScrollArea, Text, Tooltip,
} from "@mantine/core";
import { IconLayoutSidebarRightCollapse, IconLayoutSidebarRightExpand, IconUsersGroup } from "@tabler/icons-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useDidUpdate, useMediaQuery } from "@mantine/hooks";
import { Link, useParams } from "react-router-dom";

import type { GroupMessage } from "../../api/contracts";
import {
  useGetAgentsQuery,
  useGetGroupQuery,
  usePostGroupMessageMutation,
  useSetGroupFavoriteMutation,
} from "../../api/kondenApi";
import { useAppDispatch, useAppSelector } from "../../app/hooks";
import {
  groupOutgoingConfirmed,
  groupOutgoingFailed,
  groupOutgoingHydrated,
  groupOutgoingQueued,
  groupOutgoingRemoved,
  type OutgoingGroupMessage,
} from "../../realtime/runtimeSlice";
import { MarkdownMessage } from "../conversation/MarkdownMessage";
import { formatMessageTime } from "../conversation/messageTime";
import {
  loadFailedGroupMessages,
  removeFailedGroupMessage,
  saveFailedGroupMessages,
  upsertFailedGroupMessage,
} from "../conversation/outgoingStorage";
import { AgentStatus } from "../sessions/AgentStatus";
import { GroupComposer, type RestoredGroupDraft } from "./GroupComposer";
import { BotAvatar, ChatPresentation, useIdentityColor, messageReference, MessageQuote, MessageMenu, type MessageReference } from "../conversation/ChatPresentation";
import { useMessageReading } from "../conversation/useMessageReading";
import { createClientId } from "../conversation/clientId";

const groupSendChains = new Map<string, Promise<void>>();

function enqueueGroupSend(key: string, task: () => Promise<void>): Promise<void> {
  const previous = groupSendChains.get(key) ?? Promise.resolve();
  const next = previous.catch(() => {}).then(task);
  const tracked = next.finally(() => {
    if (groupSendChains.get(key) === tracked) groupSendChains.delete(key);
  });
  groupSendChains.set(key, tracked);
  return next;
}

function groupErrorMessage(cause: unknown, fallback: string): string {
  if (typeof cause === "string" && cause.trim() !== "") return cause;
  if (cause !== null && typeof cause === "object") {
    const detail = (cause as { data?: { detail?: unknown } }).data?.detail;
    if (typeof detail === "string" && detail.trim() !== "") return detail;
    const message = (cause as { message?: unknown }).message;
    if (typeof message === "string" && message.trim() !== "") return message;
  }
  return fallback;
}

type VisibleGroupMessage = {
  message: GroupMessage;
  deliveryState?: "sending" | "failed";
  deliveryError?: string | null;
  deliveryId?: string;
};

function GroupMessageRow({ entry, source, groupId, groupName, retryDisabled, onRetry, onRestore, onDiscard }: {
  entry: VisibleGroupMessage;
  source?: GroupMessage;
  groupId: string;
  groupName: string;
  retryDisabled?: boolean;
  onRetry?: () => void;
  onRestore?: () => void;
  onDiscard?: () => void;
}) {
  const { message, deliveryState, deliveryError } = entry;
  const time = formatMessageTime(message.occurred_at);
  const identityColor = useIdentityColor();
  const session = message.sender_session_id ?? undefined;
  const bubbleClass = deliveryState === "sending"
    ? "group-message-bubble is-sending"
    : deliveryState === "failed"
      ? "group-message-bubble is-failed"
      : "group-message-bubble";
  const bubble = (
    <div className={bubbleClass} tabIndex={0}>
      {source === undefined ? null : <MessageQuote quote={messageReference(source.message_id, source.sender ?? "你", source.text)} />}
      {message.mentioned_agents.every((name) => message.text.includes(`@${name}`)) ? null : <Group gap={4} mb={6}>
        {message.mentioned_agents.filter((name) => !message.text.includes(`@${name}`)).map((name) => <span key={name} className="message-mention" data-identity={identityColor(name)}>@{name}</span>)}
      </Group>}
      <MarkdownMessage content={message.text} streaming={false} />
    </div>
  );
  return (
    <article data-message-id={message.message_id} className={message.sender === null ? "group-message is-human" : "group-message"}
      aria-label={`${message.sender ?? "你"}的群消息`}>
      <div className="message group-message-row">
        {message.sender === null ? <span className="agent-avatar human-avatar" aria-hidden="true">你</span> : <BotAvatar name={message.sender} sessionId={session} />}
        {deliveryState === undefined ? (
          <MessageMenu id={message.message_id} author={message.sender ?? "你"} text={message.text}>
            {bubble}
          </MessageMenu>
        ) : bubble}
      </div>
      <div className="turn-details group-message-meta">
        {message.sender === null ? null : session === undefined ? (
          <Text data-identity={identityColor(message.sender, session)} fw={600} fz={12}>{message.sender}</Text>
        ) : (
          <Text component={Link} data-identity={identityColor(message.sender, session)} fw={600} fz={12} to={`/sessions/${encodeURIComponent(session)}`} state={{ fromGroup: { id: groupId, name: groupName } }}>
            {message.sender}
          </Text>
        )}
        {deliveryState === "sending" ? (
          <Text className="message-delivery-status" c="dimmed" fz={11} aria-live="polite">发送中…</Text>
        ) : deliveryState === "failed" ? (
          <div className="message-delivery-failed" role="alert">
            <Text className="message-delivery-error" c="red" fz={12}>{deliveryError ?? "发送没有完成"}</Text>
            <Group gap={6} wrap="nowrap">
              {onRetry === undefined ? null : <Button size="compact-xs" variant="light" color="red" disabled={retryDisabled} onClick={onRetry}>重试</Button>}
              {onRestore === undefined ? null : <Button size="compact-xs" variant="subtle" color="gray" onClick={onRestore}>放回输入框</Button>}
              {onDiscard === undefined ? null : <Button size="compact-xs" variant="subtle" color="gray" onClick={onDiscard}>删除</Button>}
            </Group>
          </div>
        ) : (
          <Text className="message-time" component="time" dateTime={message.occurred_at} title={`发送于 ${time.full}`} c="dimmed" fz={11}>{time.short}</Text>
        )}
      </div>
    </article>
  );
}

export function GroupChatPage() {
  const groupId = useParams().groupId!;
  return <GroupConversation key={groupId} groupId={groupId} />;
}

const EMPTY_GROUP_OUTGOING: OutgoingGroupMessage[] = [];

function GroupConversation({ groupId }: { groupId: string; }) {
  const dispatch = useAppDispatch();
  const connectionId = useAppSelector((state) => state.runtime.connectionId);
  const outgoing = useAppSelector((state) => state.runtime.groupOutgoing[groupId] ?? EMPTY_GROUP_OUTGOING);
  const { data: group, error: loadError } = useGetGroupQuery(groupId);
  const { data: agents = [] } = useGetAgentsQuery();
  const [postMessage] = usePostGroupMessageMutation();
  const [setFavorite, savingFavorite] = useSetGroupFavoriteMutation();
  const [failure, setFailure] = useState<string | null>(null);
  const [detailsOpened, setDetailsOpened] = useState(false);
  const [quote, setQuote] = useState<MessageReference | null>(null);
  const [restoredDraft, setRestoredDraft] = useState<RestoredGroupDraft | null>(null);
  const hydratedRef = useRef(false);
  const narrow = useMediaQuery("(max-width: 73.75em)");
  useDidUpdate(() => setDetailsOpened(false), [narrow]);

  useEffect(() => {
    if (group === undefined) return;
    const committedIds = new Set(group.messages.map((item) => item.message_id));
    if (!hydratedRef.current) {
      hydratedRef.current = true;
      const stored = loadFailedGroupMessages(groupId);
      const remaining = stored.filter((item) => !committedIds.has(`group-message-${item.deliveryId}`));
      if (remaining.length > 0) {
        dispatch(groupOutgoingHydrated({ groupId, messages: remaining }));
      }
      if (remaining.length !== stored.length) {
        saveFailedGroupMessages(groupId, remaining);
      }
    }
    for (const item of outgoing) {
      if (committedIds.has(`group-message-${item.deliveryId}`)) {
        dispatch(groupOutgoingConfirmed({ groupId, deliveryId: item.deliveryId }));
        removeFailedGroupMessage(groupId, item.deliveryId);
      }
    }
  }, [dispatch, group, groupId, outgoing]);

  const visibleMessages = useMemo<VisibleGroupMessage[]>(() => {
    if (group === undefined) return [];
    const committedIds = new Set(group.messages.map((item) => item.message_id));
    const entries: VisibleGroupMessage[] = group.messages.map((message) => ({ message }));
    for (const item of outgoing) {
      const messageId = `group-message-${item.deliveryId}`;
      if (committedIds.has(messageId)) continue;
      entries.push({
        message: {
          message_id: messageId,
          sender: null,
          sender_session_id: null,
          text: item.text,
          mentioned_agents: item.mention,
          occurred_at: item.createdAt,
          reply_to: null,
        },
        deliveryState: item.status,
        deliveryError: item.error,
        deliveryId: item.deliveryId,
      });
    }
    return entries;
  }, [group, outgoing]);

  const readingMessages = useMemo(() => visibleMessages.map((entry) => entry.message), [visibleMessages]);
  const reading = useMessageReading(groupId, readingMessages);

  if (loadError !== undefined) {
    return <Text c="dimmed" p="lg">这个群不存在或已无法读取。</Text>;
  }
  if (group === undefined) {
    return <div className="conversation-loading"><Loader size="sm" /><Text c="dimmed" size="sm">正在打开群聊…</Text></div>;
  }

  function submitOutgoing(message: OutgoingGroupMessage, activeConnectionId: string) {
    const targetGroupId = groupId;
    dispatch(groupOutgoingQueued({ groupId: targetGroupId, message: { ...message, status: "sending", error: null } }));
    reading.latest();
    return enqueueGroupSend(`group:${targetGroupId}`, async () => {
      try {
        await postMessage({
          connectionId: activeConnectionId,
          groupId: targetGroupId,
          text: message.text,
          mention: message.mention,
          deliveryId: message.deliveryId,
        }).unwrap();
        removeFailedGroupMessage(targetGroupId, message.deliveryId);
      } catch (cause) {
        const error = groupErrorMessage(cause, "发送没有完成");
        dispatch(groupOutgoingFailed({
          groupId: targetGroupId,
          deliveryId: message.deliveryId,
          error,
        }));
        upsertFailedGroupMessage(targetGroupId, { ...message, status: "failed", error });
      }
    });
  }

  function send(text: string, mention: string[]) {
    if (connectionId === null) {
      return;
    }
    const message: OutgoingGroupMessage = {
      deliveryId: `web-${createClientId()}`,
      text,
      mention,
      createdAt: new Date().toISOString(),
      status: "sending",
      error: null,
    };
    void submitOutgoing(message, connectionId);
  }

  function retryOutgoing(deliveryId: string) {
    if (connectionId === null) return;
    const message = outgoing.find((item) => item.deliveryId === deliveryId);
    if (message === undefined) return;
    void submitOutgoing(message, connectionId);
  }

  function restoreOutgoing(deliveryId: string) {
    const message = outgoing.find((item) => item.deliveryId === deliveryId);
    if (message === undefined) return;
    setRestoredDraft({ id: createClientId(), text: message.text });
    dispatch(groupOutgoingRemoved({ groupId, deliveryId }));
    removeFailedGroupMessage(groupId, deliveryId);
  }

  function discardOutgoing(deliveryId: string) {
    dispatch(groupOutgoingRemoved({ groupId, deliveryId }));
    removeFailedGroupMessage(groupId, deliveryId);
  }

  const details = <>
        <div className="agent-details-profile">
          <span className="agent-avatar agent-details-avatar"><IconUsersGroup size={30} /></span>
          <Text fw={600} size="md">{group.name}</Text>
          <Text c="dimmed" fz={12}>{group.favorite ? "常用讨论组" : "临时讨论组"} · {group.members.length} 位成员</Text>
          {!group.dissolved && <Button size="compact-xs" variant="subtle" disabled={!connectionId} loading={savingFavorite.isLoading}
            onClick={async () => {
              if (!connectionId) return;
              setFailure(null);
              try {
                await setFavorite({ connectionId, groupId, favorite: !group.favorite }).unwrap();
              } catch (cause) {
                setFailure(typeof cause === "string" ? cause : "更改讨论组类型失败");
              }
            }}>{group.favorite ? "改为临时讨论组" : "设为常用讨论组"}</Button>}
        </div>
        <div className="agent-details-section">
          <Text className="agent-details-label" size="xs" c="dimmed">群成员</Text>
          <div className="group-member-list">{group.members.map((name) => {
            const session = group.member_sessions[name];
            const agent = agents.find((entry) => entry.session_id === session);
            const identity = <>
              <BotAvatar name={name} sessionId={session} />
              <div className="group-member-identity"><Text fz={13} fw={500} truncate>{name}</Text></div>
              {agent === undefined ? null : <AgentStatus agent={agent} />}
            </>;
            return (
              <Link className="group-member" key={name} aria-label={`查看 ${name} 的会话`}
                to={`/sessions/${encodeURIComponent(session)}`} state={{ fromGroup: { id: groupId, name: group.name } }}>{identity}</Link>
            );
          })}</div>
        </div>
      </>;
  return (
    <ChatPresentation value={{ names: group.members, quote, onQuote: setQuote, onJump: reading.jump }}>
    <div className="session-page group-chat">
      <div className="session-main">
        <header className="page-header session-header session-header-overlay">
          <Group gap={10} wrap="nowrap" className="session-header-identity">
            <span className="agent-avatar agent-avatar-small"><IconUsersGroup size={18} /></span>
            <div className="session-header-name">
              <Text fw={600} size="sm" truncate>{group.name}</Text>
              <Text c="dimmed" fz={11}>{group.members.length} 位 Bot · {group.messages.length} 条消息</Text>
            </div>
          </Group>
          <div className="group-header-members" aria-label="群成员">{group.members.map((name) => <BotAvatar key={name} name={name} sessionId={group.member_sessions[name]} />)}</div>
          <Tooltip label={detailsOpened ? "收起群资料" : "显示群资料"}>
            <ActionIcon className="agent-details-toggle" aria-label={detailsOpened ? "收起群资料" : "显示群资料"}
              aria-expanded={detailsOpened} onClick={() => setDetailsOpened((opened) => !opened)} variant="subtle" color="gray">
              {detailsOpened ? <IconLayoutSidebarRightCollapse size={18} /> : <IconLayoutSidebarRightExpand size={18} />}
            </ActionIcon>
          </Tooltip>
        </header>
        <ScrollArea className="group-chat-scroll" viewportRef={reading.viewport} type="hover"
          onScrollPositionChange={({ y }) => reading.onScroll(y)}>
          <div className="group-transcript" role="region" aria-label="聊天消息">
            {visibleMessages.length === 0 ? (
              <div className="group-chat-empty">
                <span className="agent-avatar agent-details-avatar"><IconUsersGroup size={30} /></span>
                <Text fw={500} size="lg">和 {group.members.join("、")} 一起开始</Text>
                <Text c="dimmed" size="sm">在这里分配任务、同步进展，或点名请某位成员回应。</Text>
              </div>
            ) : visibleMessages.map((entry, index) => <div className="group-message-slot" key={entry.message.message_id}>
              {index > 0 && visibleMessages[index - 1].message.message_id === reading.unreadAfter ? <div className="unread-divider" role="separator">以下是未读消息</div> : null}
              <GroupMessageRow
                entry={entry}
                source={group.messages.find((item) => item.message_id === entry.message.reply_to)}
                groupId={groupId}
                groupName={group.name}
                retryDisabled={connectionId === null}
                onRetry={entry.deliveryId === undefined ? undefined : () => retryOutgoing(entry.deliveryId!)}
                onRestore={entry.deliveryId === undefined ? undefined : () => restoreOutgoing(entry.deliveryId!)}
                onDiscard={entry.deliveryId === undefined ? undefined : () => discardOutgoing(entry.deliveryId!)}
              />
            </div>)}
          </div>
        </ScrollArea>
        <div className="group-composer-dock">
          {reading.canReturn ? <Button className="jump-to-latest quote-return" variant="default" size="compact-sm" onClick={reading.returnToReading}>返回刚才的位置</Button> : reading.atBottom ? null : <Button className="jump-to-latest" variant="default" size="compact-sm" onClick={reading.latest}>{reading.pending > 0 ? `${reading.pending} 条新消息` : "回到最新"} ↓</Button>}
          <div className="group-composer-status" aria-live="polite">
            {group.calling.length === 0 ? null : (
              <Text c="dimmed" fz={12}>正在通知 {group.calling.join("、")}…</Text>
            )}
          </div>
          {failure === null ? null : <Alert color="red">{failure}</Alert>}
          {group.dissolved ? <Text size="sm" c="dimmed" ta="center" py="md">群聊已解散，此处仅保留聊天记录。</Text>
            : group.members.length === 0 ? <Text size="sm" c="dimmed" ta="center" py="md">群里已没有 Bot，可从侧栏菜单解散群聊。</Text>
            : <GroupComposer groupId={groupId} groupName={group.name} members={group.members} connected={connectionId !== null}
              restoredDraft={restoredDraft} onRestoredDraftConsumed={() => setRestoredDraft(null)} onSend={send} />}
        </div>
      </div>
      {narrow ? <Drawer opened={detailsOpened} onClose={() => setDetailsOpened(false)} title="群资料" position="right" size="min(292px, 85vw)" classNames={{ header: "details-heading", body: "details-body" }} closeButtonProps={{ "aria-label": "关闭群资料" }}>{details}</Drawer>
        : !detailsOpened ? null : <aside className="agent-details" aria-label="群资料"><header className="details-heading"><Text fw={600} fz={12}>群资料</Text><ActionIcon aria-label="关闭群资料" variant="subtle" color="gray" onClick={() => setDetailsOpened(false)}><IconLayoutSidebarRightCollapse size={17} /></ActionIcon></header>{details}</aside>}
    </div>
    </ChatPresentation>
  );
}
