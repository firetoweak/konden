import { Alert, Button, Group, Input, Modal, NativeSelect, Stack, Text, TextInput } from "@mantine/core";
import { IconAdjustments } from "@tabler/icons-react";
import { useId, useState } from "react";

import { useCreateAgentMutation, useGetModelSettingsQuery, useGetWorkspacesQuery } from "../../api/kondenApi";
import { configuredModel, providerName } from "../models/modelUi";
import { ProjectDirectoryInput } from "./ProjectDirectoryInput";

export function CreateAgentModal({
  opened, configuring, returnedFromSettings, connectionId, onConfigureModels, onClose, onCreated,
}: {
  opened: boolean;
  configuring: boolean;
  returnedFromSettings: boolean;
  connectionId: string | null;
  onConfigureModels: () => void;
  onClose: () => void;
  onCreated: (sessionId: string) => void;
}) {
  const [name, setName] = useState("");
  const [path, setPath] = useState("");
  const [model, setModel] = useState("");
  const [selecting, setSelecting] = useState(false);
  const { data: workspaces = [] } = useGetWorkspacesQuery(undefined, { skip: !opened && !configuring });
  const { data: settings, isError: modelSettingsFailed, refetch } = useGetModelSettingsQuery(undefined,
    { skip: !opened && !configuring, refetchOnMountOrArgChange: true, pollingInterval: opened ? 5000 : 0 });
  const modelId = useId();
  const [createAgent, agentRequest] = useCreateAgentMutation();
  const isLoading = agentRequest.isLoading;
  const [error, setError] = useState<string | null>(null);
  const defaultModel = settings?.config.model.default;
  const configuredProviders = new Set(settings?.providers.filter((item) => item.configured).map((item) => item.provider));
  const models = (settings?.config.model.candidates ?? []).map(configuredModel)
    .filter((item) => configuredProviders.has(item.provider));
  const usableDefault = models.find((item) => item.id === defaultModel);
  const unavailableSelection = model !== "" && !models.some((item) => item.id === model);
  const modelLabel = !settings ? (modelSettingsFailed ? "读取模型失败" : "正在读取模型…")
    : models.length === 0 ? "无可用模型" : usableDefault ? `使用默认模型 · ${usableDefault.name}` : "默认模型不可用";
  const modelOptions = [
    { value: "", label: modelLabel, disabled: !usableDefault && models.length > 0 },
    ...(unavailableSelection ? [{ value: model, label: "请重新选择模型", disabled: true }] : []),
    ...models.filter((item) => item !== usableDefault || item.id === model)
      .map((item) => ({ value: item.id, label: `${item.name}（${providerName(item.provider)}）` })),
  ];
  const modelReady = !!settings && !modelSettingsFailed && (model === "" ? !!usableDefault : !unavailableSelection);
  const canSubmit = connectionId !== null && name.trim() !== "" && modelReady && !selecting && !isLoading;

  function clearDraft() {
    setName("");
    setPath("");
    setModel("");
    setError(null);
  }

  function close() {
    clearDraft();
    agentRequest.reset();
    onClose();
  }

  async function submit() {
    if (!canSubmit || connectionId === null) return;
    setError(null);
    try {
      const agent = await createAgent({ connectionId, path: path.trim() || null, name,
        ...(model ? { model } : {}) }).unwrap();
      clearDraft();
      onCreated(agent.session_id);
    } catch (cause) {
      setError(typeof cause === "string" ? cause : "创建 Bot 失败");
      void refetch();
    }
  }

  return (
    <Modal onClose={close} opened={opened} title="新建 Bot" returnFocus={!configuring}
      closeOnClickOutside={!selecting && !isLoading} closeOnEscape={!selecting && !isLoading}
      withCloseButton={!selecting && !isLoading}>
      <Stack gap="sm">
        <TextInput label="名字" description="名字唯一，区分大小写，不能带首尾空白；自动准备独立工作目录"
          data-autofocus={!returnedFromSettings || undefined}
          disabled={isLoading || selecting} value={name} onChange={(event) => setName(event.currentTarget.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void submit();
            }
          }} />
        <ProjectDirectoryInput label="代码目录（可选）" value={path} onChange={setPath}
          workspaces={workspaces} disabled={isLoading} onSelectingChange={setSelecting} />
        <Input.Wrapper id={modelId}>
          <Group justify="space-between" gap="xs" mb={5} className="create-agent-model-header">
            <Input.Label htmlFor={modelId}>模型</Input.Label>
            <Button variant="subtle" size="compact-sm" disabled={isLoading || selecting}
              data-autofocus={returnedFromSettings || undefined}
              leftSection={<IconAdjustments size={15} />} onClick={onConfigureModels}>模型配置</Button>
          </Group>
          <NativeSelect id={modelId} aria-describedby={`${modelId}-hint`} value={model} data={modelOptions}
            disabled={isLoading || selecting || modelSettingsFailed || models.length === 0}
            onChange={(event) => setModel(event.currentTarget.value)} />
          {settings && !modelSettingsFailed ? <Text id={`${modelId}-hint`} mt={5} size="xs" c="dimmed">
            {models.length === 0 ? "请先在「模型配置」中添加一个可用模型。"
              : unavailableSelection ? "原选模型已不可用，请重新选择。"
              : !usableDefault && model === "" ? "请选择一个可用模型，或在「模型配置」中更换默认模型。"
              : "创建后可以在对话中更换。"}
          </Text> : null}
          {modelSettingsFailed ? <Button onClick={() => void refetch()} variant="subtle" size="compact-xs" mt={5}>
            重新读取模型
          </Button> : null}
        </Input.Wrapper>
        {error === null ? null : <Alert color="red" title="无法创建">{error}</Alert>}
        <Button loading={isLoading} disabled={!canSubmit} onClick={() => void submit()}>创建</Button>
      </Stack>
    </Modal>
  );
}
