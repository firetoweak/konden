import { ActionIcon, Group, Text, Textarea, Tooltip } from "@mantine/core";
import { IconAt, IconArrowUp } from "@tabler/icons-react";
import { useContext, useEffect, useRef, useState } from "react";
import { ChatPresentation, MessageQuote, splitMessageQuote, withMessageQuote } from "../conversation/ChatPresentation";
import { clearSentComposerText, restoreComposerText, saveComposerText } from "../conversation/composerDraft";
import { useMentionCompletion } from "../conversation/MentionCompletion";

export type RestoredGroupDraft = {
  id: string;
  text: string;
};

export function GroupComposer({ groupId, groupName, members, connected, restoredDraft = null, onRestoredDraftConsumed, onSend }: {
  groupId?: string;
  groupName: string;
  members: string[];
  connected: boolean;
  restoredDraft?: RestoredGroupDraft | null;
  onRestoredDraftConsumed?: () => void;
  onSend: (text: string, mention: string[]) => void;
}) {
  const draftKey = `group:${groupId ?? groupName}`;
  const [draft, setDraft] = useState(() => restoreComposerText(draftKey));
  const { quote: requestedQuote, onQuote } = useContext(ChatPresentation);
  const { body: text, quote } = splitMessageQuote(draft);
  const setText = (body: string) => setDraft(withMessageQuote(body, quote));
  const consumedRestoreIdRef = useRef<string | null>(null);
  useEffect(() => { saveComposerText(draftKey, draft); }, [draftKey, draft]);
  useEffect(() => {
    if (requestedQuote === null) return;
    setDraft((current) => withMessageQuote(splitMessageQuote(current).body, requestedQuote));
    onQuote(null);
    textarea.current?.focus();
  }, [requestedQuote, onQuote]);
  useEffect(() => {
    if (restoredDraft === null || consumedRestoreIdRef.current === restoredDraft.id) return;
    consumedRestoreIdRef.current = restoredDraft.id;
    setDraft((current) => {
      if (current.trim() === "") return restoredDraft.text;
      if (restoredDraft.text.trim() === "") return current;
      return `${current}\n${restoredDraft.text}`;
    });
    onRestoredDraftConsumed?.();
    textarea.current?.focus();
  }, [onRestoredDraftConsumed, restoredDraft]);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const mentions = useMentionCompletion({ text, onChange: setText, names: members, textarea, includeAll: true });
  const names = [...members].sort((a, b) => b.length - a.length).map((name) => name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const addressed = [...text.matchAll(new RegExp(`(^|[^A-Za-z0-9._%+-])@(${names})(?=$|[\\s，。！？、,:;!?）)])`, "giu"))]
    .map((item) => members.find((name) => name === item[2]) ?? members.find((name) => name.toLocaleLowerCase() === item[2].toLocaleLowerCase()));
  const mentionAll = /(^|[^A-Za-z0-9._%+-])@all(?=$|[\s，。！？、,:;!?）)])/iu.test(text);
  const mention = mentionAll ? [] : members.filter((name) => addressed.includes(name));
  const canSend = connected && text.trim() !== "";

  function send() {
    if (!canSend) return;
    const sentDraft = draft;
    const sentMention = mention;
    clearSentComposerText(draftKey, sentDraft);
    setDraft("");
    onSend(sentDraft, sentMention);
  }

  return <div className={`composer group-composer${quote !== null || text.length > 90 || text.includes("\n") ? " is-expanded" : ""}`}>
    {quote === null ? null : <MessageQuote quote={quote} onRemove={() => setDraft(text)} />}
    {mentions.menu}
    <Textarea className="composer-input" ref={textarea} aria-label="群消息" autosize maxRows={7} minRows={quote !== null || text.length > 90 || text.includes("\n") ? 2 : 1}
      {...mentions.inputProps}
      onKeyDown={(event) => {
        if (mentions.handleKeyDown(event)) return;
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          void send();
        }
      }}
      placeholder={`发送到${groupName}，输入 @ 点名成员…`} variant="unstyled" value={text} />
    <Group className="composer-meta" justify="space-between" wrap="nowrap" gap="xs">
      <Group gap={6} wrap="nowrap" className="group-composer-recipients">
        <ActionIcon aria-label="点名成员" variant="subtle" color="gray" radius="xl" size={27} onClick={mentions.insertTrigger}><IconAt size={17} /></ActionIcon>
        <Text c="dimmed" fz={12} truncate aria-live="polite">
          {mention.length === 0 ? "群消息 · 点名可选" : `发送给 ${mention.map((name) => `@${name}`).join("、")}`}
        </Text>
      </Group>
      <Tooltip label="发送到群">
        <ActionIcon className="composer-send" aria-label="发送到群" disabled={!canSend}
          radius="xl" size={32} onClick={send}>
          <IconArrowUp aria-hidden="true" size={18} stroke={2} />
        </ActionIcon>
      </Tooltip>
    </Group>
  </div>;
}
