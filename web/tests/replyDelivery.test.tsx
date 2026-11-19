import { MantineProvider } from "@mantine/core";
import { configureStore } from "@reduxjs/toolkit";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ComponentProps } from "react";
import { Provider } from "react-redux";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import type { ConversationView } from "../src/api/contracts";
import { kondenApi } from "../src/api/kondenApi";
import { Conversation } from "../src/features/conversation/Conversation";
import type { ConversationTimeline } from "../src/features/conversation/ConversationTimeline";
import runtimeReducer, {
  connected, outputFinal, sessionActivity, viewing,
} from "../src/realtime/runtimeSlice";

// Markdown rendering, the editor and virtualization are outside the reply delivery contract.
vi.mock("../src/features/conversation/MarkdownMessage", () => ({
  MarkdownMessage: ({ content }: { content: string }) => <span>{content}</span>,
}));
vi.mock("../src/features/conversation/Composer", () => ({ Composer: () => null }));
vi.mock("../src/features/conversation/ConversationTimeline", () => ({
  ConversationTimeline: ({ turns, renderTurn }: ComponentProps<typeof ConversationTimeline>) => (
    <>{turns.map((turn) => <div key={turn.key}>{renderTurn(turn)}</div>)}</>
  ),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("同事私信留在原线程，显式发给用户的消息可见并计入未读", async () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  const NativeRequest = Request;
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  });
  const view: ConversationView = {
    session_id: "agent", workspace_id: "workspace", revision: 6,
    compact_count: 0, compact_phase: null, waiting_until: null, work_plan: null, work_plan_updates: [], context_input_tokens: null,
    deleted: false, agent_name: "Atlas",
    session: {
      status: "waiting", waiting_for: ["external_fact"], pending_authorization_ids: [], pending_authorization_commands: [],
      should_wake: false, has_open_subagents: false, control_approval: null,
      control_message: null, auto_authorize: false, last_error: null,
    },
    items: [
      { kind: "user", message_id: "hi", text: "你好", occurred_at: "2026-10-19T00:00:00Z", images: [], files: [] },
      { kind: "step", step_id: "s1", output_id: "send-hi", text: "回给人", thinking: null,
        occurred_at: "2026-10-19T00:00:01Z", tools: [] },
      { kind: "private_message", message_id: "received-lin", direction: "received", peer: "Lin", peer_session_id: "lin",
        text: "请检查文件", thread_id: "lin-thread", reply_to: null,
        occurred_at: "2026-10-19T00:01:00Z" },
      { kind: "step", step_id: "s2", output_id: "colleague", text: null, thinking: null,
        occurred_at: "2026-10-19T00:01:00Z", tools: [{ command_id: "read", name: "read_file", status: "succeeded",
          error: null, arguments: { path: "a.md" } }] },
      { kind: "private_message", message_id: "sent-lin", direction: "sent", peer: "Lin", peer_session_id: "lin",
        text: "文件没问题", thread_id: "lin-thread", reply_to: "received-lin",
        occurred_at: "2026-10-19T00:01:04Z" },
      { kind: "step", step_id: "s3:tell", output_id: "tell", text: "结论给你", thinking: null,
        occurred_at: "2026-10-19T00:01:05Z", tools: [] },
    ],
  };
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (path === "/api/agents") return Response.json([
      { session_id: "agent-lin", projects: [], name: "Lin", activity: "idle", status: "idle" },
    ]);
    if (path.endsWith("/routines") || path.endsWith("/commands")) return Response.json([]);
    return Response.json(view);
  }));
  const store = configureStore({
    reducer: { runtime: runtimeReducer, [kondenApi.reducerPath]: kondenApi.reducer },
    middleware: (getDefaultMiddleware) => getDefaultMiddleware().concat(kondenApi.middleware),
  });
  store.dispatch(connected("connection"));
  render(
    <Provider store={store}><MantineProvider env="test">
      <MemoryRouter initialEntries={["/sessions/agent"]}>
        <Routes><Route path="/sessions/:sessionId" element={<Conversation />} /></Routes>
      </MemoryRouter>
    </MantineProvider></Provider>,
  );

  expect(await screen.findByText("回给人")).toBeInTheDocument();
  expect(screen.getByText("结论给你")).toBeInTheDocument();
  expect(screen.queryByText("这一轮没有文字回复")).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "打开 Lin 的对话" })).not.toBeInTheDocument();
  expect(screen.queryByText("请检查文件")).not.toBeInTheDocument();
  expect(screen.queryByText("文件没问题", { exact: true })).not.toBeInTheDocument();
  expect(screen.getByRole("img", { name: "消息来自" })).toBeVisible();
  expect(screen.getAllByRole("button", { name: /Lin，查看私聊/ })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "消息来自 Lin，查看私聊" }));
  expect(screen.getByRole("dialog", { name: "与 Lin 的私聊" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "与 Lin 的私聊" })).not.toBeInTheDocument();
  expect(screen.queryByText("2 条消息")).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "打开 Lin 的对话" })).not.toBeInTheDocument();
  expect(screen.getByText("请检查文件")).toBeVisible();
  expect(screen.getByText("文件没问题", { exact: true })).toBeVisible();
  expect(screen.getAllByRole("article", { name: /的私信$/ }).map((item) => item.getAttribute("aria-label")))
    .toEqual(["Lin 发给 Atlas 的私信", "Atlas 发给 Lin 的私信"]);
  act(() => {
    store.dispatch(kondenApi.util.updateQueryData("getConversation", "agent", (draft) => {
      draft.items.push({ kind: "private_message", message_id: "follow-up", thread_id: "lin-thread",
        direction: "received", peer: "Lin", peer_session_id: "lin", text: "收到，检查通过", reply_to: "sent-lin",
        occurred_at: "2026-10-19T00:02:00Z" });
    }));
  });
  expect(await screen.findByText("收到，检查通过")).toBeVisible();
  expect(screen.getAllByRole("article", { name: /的私信$/ })).toHaveLength(3);
  expect(screen.getAllByRole("dialog", { name: "与 Lin 的私聊" })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "关闭私聊" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.queryByText("请检查文件")).not.toBeInTheDocument();
  expect(screen.queryByText("文件没问题", { exact: true })).not.toBeInTheDocument();
  expect(screen.queryByText("收到，检查通过")).not.toBeInTheDocument();
  expect(screen.queryByText("以下是未读消息")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /工作记录/ })).toBeNull();

  act(() => {
    store.dispatch(viewing(null));
    store.dispatch(sessionActivity({ sessionId: "agent", activity: "running" }));
  });
  expect(store.getState().runtime.sessions.agent.unread).toBe(0);
  act(() => {
    store.dispatch(outputFinal({ sessionId: "agent", outputId: "send-report", text: "例行检查发现一个待确认的差额" }));
  });
  expect(store.getState().runtime.sessions.agent.unread).toBe(1);
  expect(screen.getByText("例行检查发现一个待确认的差额")).toBeVisible();
  cleanup();
  store.dispatch(kondenApi.util.resetApiState());
});
