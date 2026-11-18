import { Button, Group, Stack, Text, Textarea } from "@mantine/core";
import { useId, useState } from "react";
import { apiErrorDetail, useAnswerClarificationMutation } from "../../api/kondenApi";
import { MarkdownMessage } from "./MarkdownMessage";
import type { VisibleStep } from "./visibleTimeline";

const previewCount = 3;

function AnswerChoice({ text, name, checked, disabled, onSelect }: {
  text: string; name: string; checked: boolean; disabled: boolean; onSelect: () => void;
}) {
  const [title, ...lines] = text.split(/\r?\n/);
  return <label className="clarification-choice">
    <input type="radio" name={name} checked={checked} disabled={disabled} onChange={onSelect} />
    <span>
      <span className="clarification-title">{title}</span>
      {lines.length > 0 && <span className="clarification-description">{lines.join("\n")}</span>}
    </span>
  </label>;
}

export function ClarificationCard({ question, card, sessionId, connectionId, deleted }: {
  question: string;
  card: NonNullable<VisibleStep["clarification"]>;
  sessionId: string;
  connectionId: string | null;
  deleted: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [selected, setSelected] = useState<number | "custom" | null>(null);
  const [expanded, setExpanded] = useState(false);
  const groupName = useId();
  const [submit, { isLoading, error, isSuccess }] = useAnswerClarificationMutation();
  const disabled = connectionId === null || isLoading || isSuccess;
  const { options } = card;
  const list = options.length > previewCount || options.some(option => option.includes("\n") || [...option].length > 16);
  const showInput = !list || selected === "custom";
  const response = showInput ? draft : typeof selected === "number" ? options[selected] : "";
  const lastPreview = typeof selected === "number" && selected >= previewCount ? selected : previewCount - 1;
  function answer(text: string) {
    if (connectionId !== null && text.trim() && !disabled) {
      void submit({ sessionId, connectionId, questionId: card.question_id, text: text.trim() });
    }
  }
  return <Stack gap="xs" component="section" aria-label="澄清问题">
    <Text fw={600} size="xs" c="dimmed">{card.answer !== null ? "已回答" : deleted ? "未回答" : "待你回答"}</Text>
    <MarkdownMessage content={question} streaming={false} />
    {card.answer !== null ? <Text className="pre-wrap" size="sm">你的回答 · {card.answer}</Text> : deleted ? null : <>
      {!list && options.length > 0 && <Group gap="xs">
        {options.map((option) => <Button className="clarification-option" key={option}
          variant="light" size="compact-sm" disabled={disabled} onClick={() => answer(option)}>{option}</Button>)}
      </Group>}
      <form className="clarification-form" onSubmit={(event) => { event.preventDefault(); answer(response); }}>
        {list && <div className="clarification-list" role="radiogroup" aria-label="回答选项">
          {options.map((option, index) => (expanded || index < previewCount - 1 || index === lastPreview) &&
            <AnswerChoice key={option} text={option} name={groupName} checked={selected === index}
              disabled={disabled} onSelect={() => setSelected(index)} />)}
          {options.length > previewCount && <Button type="button" variant="subtle" size="compact-sm"
            className="clarification-expand" aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>
            {expanded ? "收起选项" : `展开其余 ${options.length - previewCount} 项`}
          </Button>}
          <AnswerChoice text="我有其他想法" name={groupName} checked={selected === "custom"}
            disabled={disabled} onSelect={() => setSelected("custom")} />
        </div>}
        <Group align="end" justify="flex-end" gap="xs">
          {showInput && <Textarea flex={list ? "0 0 100%" : 1} autosize minRows={list ? 2 : 1} maxRows={5} aria-label="自由回答"
            placeholder={!list && options.length ? "或输入其他回答" : "写下你的回答"}
            value={draft} disabled={disabled} onChange={(event) => setDraft(event.currentTarget.value)} />}
          <Button type="submit" size="sm" variant={list ? "filled" : "subtle"}
            disabled={disabled || !response.trim()}>提交回答</Button>
        </Group>
      </form>
      {error && <Text role="alert" size="xs" c="red">
        {("data" in error && apiErrorDetail(error.data)) || "回答未能提交，请重试。"}
      </Text>}
    </>}
  </Stack>;
}
