import { Button, Group, Stack, Text, Textarea } from "@mantine/core";
import { useState } from "react";
import { apiErrorDetail, useAnswerClarificationMutation } from "../../api/kondenApi";
import { MarkdownMessage } from "./MarkdownMessage";
import type { VisibleStep } from "./visibleTimeline";

export function ClarificationCard({ question, card, sessionId, connectionId, deleted }: {
  question: string;
  card: NonNullable<VisibleStep["clarification"]>;
  sessionId: string;
  connectionId: string | null;
  deleted: boolean;
}) {
  const [draft, setDraft] = useState("");
  const [submit, { isLoading, error, isSuccess }] = useAnswerClarificationMutation();
  const disabled = connectionId === null || isLoading || isSuccess;
  function answer(text: string) {
    if (connectionId !== null && text.trim() && !disabled) {
      void submit({ sessionId, connectionId, questionId: card.question_id, text: text.trim() });
    }
  }
  return <Stack gap="xs" component="section" aria-label="澄清问题">
    <Text fw={600} size="xs" c="dimmed">{card.answer !== null ? "已回答" : deleted ? "未回答" : "待你回答"}</Text>
    <MarkdownMessage content={question} streaming={false} />
    {card.answer !== null ? <Text className="pre-wrap" size="sm">你的回答 · {card.answer}</Text> : deleted ? null : <>
      {card.options.length > 0 && <Group gap="xs">
        {card.options.map((option) => <Button className="clarification-option" key={option}
          variant="light" size="compact-sm" disabled={disabled} onClick={() => answer(option)}>{option}</Button>)}
      </Group>}
      <form className="clarification-form" onSubmit={(event) => { event.preventDefault(); answer(draft); }}>
        <Group align="end" gap="xs">
          <Textarea flex={1} autosize minRows={1} maxRows={5} aria-label="自由回答"
            placeholder={card.options.length ? "或输入其他回答" : "写下你的回答"}
            value={draft} disabled={disabled} onChange={(event) => setDraft(event.currentTarget.value)} />
          <Button type="submit" size="sm" variant="subtle" disabled={disabled || !draft.trim()}>提交回答</Button>
        </Group>
      </form>
      {error && <Text role="alert" size="xs" c="red">
        {("data" in error && apiErrorDetail(error.data)) || "回答未能提交，请重试。"}
      </Text>}
    </>}
  </Stack>;
}
