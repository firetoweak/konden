import { Alert, Button, Modal, SegmentedControl, Stack, Text, TextInput } from "@mantine/core";
import { useState } from "react";

import { useCreateA2ABotMutation, useCreateAgentMutation } from "../../api/kondenApi";
import type { Workspace } from "../../api/contracts";
import { ProjectDirectoryInput } from "./ProjectDirectoryInput";

export function CreateAgentModal({
  opened, workspaces, connectionId, onClose, onCreated,
}: {
  opened: boolean;
  workspaces: Workspace[];
  connectionId: string | null;
  onClose: () => void;
  onCreated: (sessionId: string) => void;
}) {
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [kind, setKind] = useState("agent");
  const [selecting, setSelecting] = useState(false);
  const [createAgent, agentRequest] = useCreateAgentMutation();
  const [createA2ABot, a2aRequest] = useCreateA2ABotMutation();
  const isLoading = agentRequest.isLoading || a2aRequest.isLoading;
  const codex = kind === "codex";
  const [error, setError] = useState<string | null>(null);
  const canSubmit = connectionId !== null && name.trim() !== "" && !selecting && !isLoading;

  function clearDraft() {
    setName("");
    setPath("");
    setKind("agent");
    setError(null);
  }

  function close() {
    clearDraft();
    agentRequest.reset();
    a2aRequest.reset();
    onClose();
  }

  async function submit() {
    if (!canSubmit || connectionId === null) return;
    setError(null);
    try {
      if (codex) {
        await createA2ABot({ connectionId, name }).unwrap();
        close();
        return;
      }
      const agent = await createAgent({ connectionId, path: path.trim() || null, name }).unwrap();
      clearDraft();
      onCreated(agent.session_id);
    } catch (cause) {
      setError(typeof cause === "string" ? cause : "创建 Bot 失败");
    }
  }

  return (
    <Modal onClose={close} opened={opened} title="新建 Bot"
      closeOnClickOutside={!selecting && !isLoading} closeOnEscape={!selecting && !isLoading}
      withCloseButton={!selecting && !isLoading}>
      <Stack gap="sm">
        <SegmentedControl aria-label="Bot 类型" value={kind} disabled={selecting || isLoading}
          onChange={(value) => { setKind(value); setError(null); }}
          data={[{ value: "agent", label: "Konden Bot" }, { value: "codex", label: "Codex（A2A）" }]} />
        <TextInput label="名字" description="名字唯一，区分大小写，不能带首尾空白；自动准备独立工作目录"
          disabled={isLoading || selecting} value={name} onChange={(event) => setName(event.currentTarget.value)}
          maxLength={codex ? 60 : undefined}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void submit();
            }
          }} />
        {codex ? <Text size="sm" c="dimmed">其他 Bot 可以把任务派给它。Codex 在本机独立目录工作，成果回到派单的对话。</Text>
          : <ProjectDirectoryInput label="代码目录（可选）" value={path} onChange={setPath}
            workspaces={workspaces} disabled={isLoading} onSelectingChange={setSelecting} />}
        {error === null ? null : <Alert color="red" title="无法创建">{error}</Alert>}
        <Button loading={isLoading} disabled={!canSubmit} onClick={() => void submit()}>创建</Button>
      </Stack>
    </Modal>
  );
}
