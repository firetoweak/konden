import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { UserMessage } from "../src/features/conversation/UserMessage";

const writeText = vi.fn();

beforeEach(() => {
  writeText.mockReset().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText }, userAgent: navigator.userAgent });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  Object.defineProperty(document, "fonts", { configurable: true, value: { addEventListener() {}, removeEventListener() {} } });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, "fonts");
});

it("消息时间独立展示，右键菜单复制原文", async () => {
  const path = String.raw`C:\Users\19403\.konden\state\bots\6a32b624476bf8c43728\lark.env`;
  const text = `请打开配置文件：\n\n\`\`\`\n${path}\n\`\`\`\n\n填写完成后继续。`;
  render(<MantineProvider><UserMessage
    sessionId="session" text={text}
    occurredAt="2026-10-03T08:00:05Z" images={[]} files={[]}
  /></MantineProvider>);

  const bubble = screen.getByText(path).closest(".user-message-row")!;
  const message = bubble.closest(".user-message-content")!;
  expect(screen.queryByRole("button", { name: "复制消息" })).not.toBeInTheDocument();
  fireEvent.contextMenu(bubble, { clientX: 100, clientY: 100 });
  const copy = await screen.findByRole("menuitem", { name: "复制消息" });
  expect(screen.queryByRole("button", { name: "编辑消息" })).not.toBeInTheDocument();
  expect(message.querySelector("time")).toHaveAttribute("datetime", "2026-10-03T08:00:05Z");
  expect(message.querySelector("time")).toHaveAttribute("title", expect.stringContaining("发送于"));
  fireEvent.click(copy);
  expect(writeText).toHaveBeenCalledWith(text);
});
