import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { UserMessage } from "../src/features/conversation/UserMessage";
import { ChatPresentation, splitMessageQuote, withMessageQuote } from "../src/features/conversation/ChatPresentation";

const writeText = vi.fn();

beforeEach(() => {
  writeText.mockReset().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText }, userAgent: navigator.userAgent });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  Object.defineProperty(document, "fonts", { configurable: true, value: { addEventListener() {}, removeEventListener() {} } });
});

afterEach(() => {
  window.getSelection()?.removeAllRanges();
  cleanup();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, "fonts");
});

it("引用保留当前消息选中的完整长片段，菜单获得焦点后仍能引用并定位原消息", async () => {
  const onQuote = vi.fn();
  const excerpt = "消息正文：只有群和成员，读不到聊天内容。".repeat(10);
  render(<MantineProvider><ChatPresentation value={{names:[], quote:null, onQuote, onJump:()=>{}}}>
    <UserMessage sessionId="session" messageId="source" text={`开头说明。\n\n${excerpt}\n\n结尾说明。`}
      occurredAt="2026-10-03T08:00:05Z" images={[]} files={[]} />
  </ChatPresentation></MantineProvider>);
  const paragraph = screen.getByText(excerpt);
  const range = document.createRange();
  range.selectNodeContents(paragraph);
  window.getSelection()!.addRange(range);
  fireEvent.contextMenu(paragraph, {clientX:100, clientY:100});
  const quote = await screen.findByRole("menuitem", {name:"引用 你 的选中文字"});
  window.getSelection()!.removeAllRanges();
  fireEvent.click(quote);
  expect(onQuote).toHaveBeenCalledWith({id:"source", author:"你", preview:excerpt});
  expect(splitMessageQuote(withMessageQuote("只讨论这部分。", onQuote.mock.calls[0][0])).quote)
    .toEqual({id:"source", author:"你", preview:excerpt});
});

it.each(["无选区", "另一条消息的选区", "跨消息选区"])("%s 不冒充当前消息的片段，仍能引用整条消息", async (scenario) => {
  const onQuote = vi.fn();
  render(<MantineProvider><ChatPresentation value={{names:[], quote:null, onQuote, onJump:()=>{}}}>
    <UserMessage sessionId="session" messageId="source" text="当前消息的完整正文。"
      occurredAt="2026-10-03T08:00:05Z" images={[]} files={[]} />
    <p>另一条消息。</p>
  </ChatPresentation></MantineProvider>);
  const paragraph = screen.getByText("当前消息的完整正文。");
  const other = screen.getByText("另一条消息。");
  if (scenario !== "无选区") {
    const range = document.createRange();
    range.selectNodeContents(other);
    if (scenario === "跨消息选区") range.setStart(paragraph.firstChild!, 0);
    window.getSelection()!.addRange(range);
  }
  fireEvent.contextMenu(paragraph, {clientX:100, clientY:100});
  fireEvent.click(await screen.findByRole("menuitem", {name:"引用 你 的消息"}));
  expect(onQuote).toHaveBeenCalledWith({id:"source", author:"你", preview:"当前消息的完整正文。"});
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

it("链接保留浏览器原生右键菜单，打开地址不会替换当前聊天", () => {
  render(<MantineProvider><UserMessage
    sessionId="session" text="请先[**一键申请权限**](https://example.com/permissions?scope=read)，再[返回说明](#说明)。"
    occurredAt="2026-10-03T08:00:05Z" images={[]} files={[]}
  /></MantineProvider>);

  const link = screen.getByRole("link", {name:"一键申请权限"});
  expect(link).toHaveAttribute("href", "https://example.com/permissions?scope=read");
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(screen.getByRole("link", {name:"返回说明"})).not.toHaveAttribute("target");
  const event = new MouseEvent("contextmenu", {bubbles:true, cancelable:true});
  fireEvent(screen.getByText("一键申请权限"), event);
  expect(event.defaultPrevented).toBe(false);
  expect(screen.queryByRole("menuitem", {name:"复制消息"})).not.toBeInTheDocument();
});
