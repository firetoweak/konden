import { Button, Combobox, Group, InputBase, Loader, Popover, Text, Tooltip, UnstyledButton } from "@mantine/core";
import { IconCheck } from "@tabler/icons-react";
import { useState } from "react";
import { CHATGPT_PROVIDER, PROVIDERS } from "./modelUi";
import type { ChatGPTAuthorization } from "./useChatGPTAuthorization";

export function ProviderPicker({ provider, disabled, onChange, chatGPT, chatGPTConnected }: {
  provider: string;
  disabled: boolean;
  onChange(value: string): void;
  chatGPT: ChatGPTAuthorization;
  chatGPTConnected: boolean;
}) {
  const [opened, setOpened] = useState(false);
  const current = PROVIDERS.find((item) => item.value === provider)!;

  return <Popover opened={opened && !disabled} onChange={setOpened}
    position="bottom-start" width="target" shadow="sm" offset={6} trapFocus returnFocus>
    <Popover.Target>
      <InputBase component="button" type="button" label="供应商" disabled={disabled} pointer
        classNames={{ input: "provider-trigger" }} rightSection={<Combobox.Chevron />} rightSectionPointerEvents="none"
        onClick={() => setOpened((value) => !value)}>
        {current.label} · {current.caption}
      </InputBase>
    </Popover.Target>
    <Popover.Dropdown className="provider-dropdown" aria-label="选择供应商">
      {PROVIDERS.map((item) => {
        const selected = provider === item.value;
        return <div key={item.value} className="provider-option-row" data-selected={selected || undefined}>
          <UnstyledButton className="provider-option" aria-pressed={selected} data-autofocus={selected || undefined}
            onClick={() => { onChange(item.value); setOpened(false); }}>
            <IconCheck size={15} aria-hidden="true" className="provider-option-check" />
            <span>{item.label} · {item.caption}</span>
          </UnstyledButton>
          {item.value === CHATGPT_PROVIDER ? <div className="provider-authorization">
            {chatGPT.waiting ? <Group gap={5} wrap="nowrap" role="status" className="provider-authorization-wait">
              <Loader size={12} aria-hidden="true" /><span>等待授权…</span>
            </Group>
              : chatGPTConnected ? <Tooltip label="已连接">
                <span className="provider-authorization-check" role="img" aria-label="ChatGPT 订阅已连接">
                  <IconCheck size={17} aria-hidden="true" />
                </span>
              </Tooltip>
                : chatGPT.local ? <Button size="compact-xs" variant="default" data-oai-siwc-button
                  onClick={chatGPT.start}>Continue with ChatGPT</Button>
                  : <Text size="xs" c="dimmed">请在运行 konden 的电脑上授权</Text>}
          </div> : null}
        </div>;
      })}
      {chatGPT.message ? <Text size="xs" c="red" className="provider-authorization-message" role="alert">
        {chatGPT.message}
      </Text> : null}
    </Popover.Dropdown>
  </Popover>;
}
