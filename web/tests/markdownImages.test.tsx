import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MarkdownMessage } from "../src/features/conversation/MarkdownMessage";

afterEach(() => {cleanup(); vi.unstubAllGlobals();});
beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({matches:false,addEventListener(){},removeEventListener(){}}));
  vi.stubGlobal("ResizeObserver", class {observe() {} unobserve() {} disconnect() {}});
});

it.each([true, false])("长路径和嵌套列表保留完整正文及层级，streaming=%s", (streaming) => {
  const path = String.raw`C:\Users\19403\.konden\state\bots\6a32b624476bf8c43728\lark.env`;
  const content = `打开配置文件：\n\n\`\`\`\n${path}\n\`\`\`\n\n9. 读取配置\n10. 完成授权\n    - 查看文档\n    - 写入日志\n11. 继续工作`;
  const {container} = render(<MantineProvider env="test"><MarkdownMessage content={content} streaming={streaming}/></MantineProvider>);
  expect(container.querySelector("pre code")?.textContent?.trim()).toBe(path);
  expect(screen.getAllByRole("list")).toHaveLength(2);
  const ordered = container.querySelector("ol")!;
  expect(ordered).toHaveAttribute("start", "9");
  expect(ordered.querySelectorAll(":scope > li")).toHaveLength(3);
  expect(ordered.querySelectorAll("li > ul > li")).toHaveLength(2);
});

it("图文回复保留文字、图片、后文顺序，流式结束后仍能预览和下载", () => {
  const href = "/api/sessions/ada/attachments/file%3Aexample";
  const content = `这张图以深蓝作为背景。\n\n![橙色圆形](${href})\n\n橙色圆形位于画面中央，形成冷暖对比。`;
  const page = (streaming:boolean) => <MantineProvider env="test"><MarkdownMessage content={content} streaming={streaming}/></MantineProvider>;
  const {container, rerender} = render(page(true));
  for (const streaming of [true, false]) {
    rerender(page(streaming));
    const before = screen.getByText("这张图以深蓝作为背景。");
    const image = screen.getByRole("button", {name:"橙色圆形",exact:true});
    const after = screen.getByText("橙色圆形位于画面中央，形成冷暖对比。");
    expect(before.compareDocumentPosition(image) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(image.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(image.querySelector("img")).toHaveAttribute("src",href);
    expect(screen.queryByText("橙色圆形", {exact:true})).not.toBeInTheDocument();
    expect(container.querySelector("p div")).toBeNull();
  }
  fireEvent.click(screen.getByRole("button", {name:"橙色圆形",exact:true}));
  const preview = screen.getByRole("dialog", {name:/橙色圆形/});
  expect(within(preview).getByRole("img", {name:"橙色圆形"})).toHaveAttribute("src",href);
  expect(within(preview).getByRole("link", {name:"下载原图"})).toHaveAttribute("download");
});
