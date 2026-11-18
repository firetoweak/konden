import { MantineProvider } from "@mantine/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ClarificationCard } from "../src/features/conversation/ClarificationCard";

const submit = vi.hoisted(() => vi.fn());
vi.mock("../src/api/kondenApi", () => ({ useAnswerClarificationMutation: () => [submit, {}] }));
vi.mock("../src/features/conversation/MarkdownMessage", () => ({
  MarkdownMessage: ({ content }: { content: string }) => <p>{content}</p>,
}));

beforeEach(() => {
  Object.defineProperty(document, "fonts", { configurable: true, value: new EventTarget() });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

function show(options: string[]) {
  render(<MantineProvider env="test"><ClarificationCard question="选哪一种？" card={{ question_id: "q1", options, answer: null }}
    sessionId="s1" connectionId="c1" deleted={false} /></MantineProvider>);
}

it("选项多时可以展开改选，收起保留当前项，确认后提交完整原文", () => {
  const last = "线上接力\n大家按自己的时间读，并轮流补充笔记。";
  show(["自由交流", "共读一本", "主题短讲", last]);
  const confirm = screen.getByRole("button", { name: "提交回答" });
  expect(confirm).toBeDisabled();
  expect(screen.queryByRole("radio", { name: /线上接力/ })).toBeNull();
  fireEvent.click(screen.getByRole("radio", { name: "共读一本" }));
  fireEvent.click(screen.getByRole("button", { name: "展开其余 1 项" }));
  fireEvent.click(screen.getByRole("radio", { name: /线上接力/ }));
  fireEvent.click(screen.getByRole("button", { name: "收起选项" }));
  expect(screen.getByRole("radio", { name: /线上接力/ })).toBeChecked();
  expect(screen.getAllByRole("radio")).toHaveLength(4);
  expect(submit).not.toHaveBeenCalled();
  fireEvent.click(confirm);
  expect(submit).toHaveBeenCalledExactlyOnceWith({ sessionId: "s1", connectionId: "c1", questionId: "q1", text: last });
});

it.each(["大家可以带上喜欢的书到现场轮流分享一个片段", "自由交流\n每人推荐一本书。"])(
  "少量长选项也可自由回答，切换选项后保留草稿：%s", (option) => {
    show([option, "其他"]);
    const choice = screen.getAllByRole("radio")[0];
    const custom = screen.getByRole("radio", { name: "我有其他想法" });
    const confirm = screen.getByRole("button", { name: "提交回答" });
    fireEvent.click(custom);
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByRole("textbox", { name: "自由回答" }), { target: { value: "分两组读" } });
    fireEvent.click(choice);
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.click(custom);
    expect(screen.getByRole("textbox")).toHaveValue("分两组读");
    fireEvent.click(confirm);
    expect(submit).toHaveBeenCalledExactlyOnceWith({ sessionId: "s1", connectionId: "c1", questionId: "q1", text: "分两组读" });
  },
);

it("三个短选项仍然点选即答", () => {
  show(["线上", "线下", "混合"]);
  expect(screen.getByRole("textbox", { name: "自由回答" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "线上" }));
  expect(submit).toHaveBeenCalledExactlyOnceWith({ sessionId: "s1", connectionId: "c1", questionId: "q1", text: "线上" });
});
