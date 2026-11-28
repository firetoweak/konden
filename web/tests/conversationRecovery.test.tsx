import { MantineProvider } from "@mantine/core";
import { configureStore } from "@reduxjs/toolkit";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Provider } from "react-redux";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";

import { kondenApi } from "../src/api/kondenApi";
import type { ConversationView } from "../src/api/contracts";
import { Conversation } from "../src/features/conversation/Conversation";
import runtimeReducer, { connected } from "../src/realtime/runtimeSlice";
import type { ComponentProps } from "react";
import type { ConversationTimeline } from "../src/features/conversation/ConversationTimeline";

// Rendering Markdown and editing drafts are outside the recovery contract.
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

function prepareBrowser() {
  vi.stubGlobal("ResizeObserver", class {
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  vi.stubGlobal("matchMedia", () => ({
    matches: false, addEventListener() {}, removeEventListener() {},
  }));
  const NativeRequest = Request;
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  });
}

it("recovers a model failure from the conversation after refresh and retries without submitting a message", async () => {
  prepareBrowser();
  const view: ConversationView = {
    session_id: "session", workspace_id: "workspace", revision: 1,
    compact_count: 0, compact_phase: null, waiting_until: null, work_plan: null, work_plan_updates: [], context_input_tokens: null,
    deleted: false, agent_name: null,
    session: {
      status: "runnable", waiting_for: [],
      should_wake: true, has_open_subagents: false, control_approval: null, control_message: null,
 last_error: "供应商暂时不可用，请稍后再试。",
    },
    items: [{ kind: "user", message_id: "user", text: "原始请求", occurred_at: "2026-10-12T00:00:00Z", images: [], files: [] }],
  };
  const requests: { path: string; method: string; body: unknown }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    const body = request.method === "POST" ? await request.json() : null;
    requests.push({ path, method: request.method, body });
    if (path.endsWith("/retry")) {
      view.session.last_error = null;
      view.session.status = "waiting";
      view.session.waiting_for = ["external_fact"];
      view.session.should_wake = false;
    }
    return Response.json(view);
  }));
  const store = configureStore({
    reducer: { runtime: runtimeReducer, [kondenApi.reducerPath]: kondenApi.reducer },
    middleware: (getDefaultMiddleware) => getDefaultMiddleware().concat(kondenApi.middleware),
  });
  store.dispatch(connected("refreshed-connection"));
  render(<Provider store={store}><MantineProvider>
    <MemoryRouter initialEntries={["/sessions/session"]}>
      <Routes><Route path="/sessions/:sessionId" element={<Conversation />} /></Routes>
    </MemoryRouter>
  </MantineProvider></Provider>);
  expect(await screen.findByText(/执行已停止。供应商暂时不可用/)).toBeInTheDocument();
  await waitFor(() => expect(store.getState().runtime.ownerSessionId).toBe("session"));
  // 刷新后的状态只来自 GET / conversation，没有 session_failed 事件。
  expect(store.getState().runtime.sessions.session.lastError).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "查看详情" }));
  expect(screen.getByRole("region", { name: "执行已停止 · 错误详情" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "关闭内容页" }));
  expect(screen.queryByRole("region", { name: "执行已停止 · 错误详情" })).not.toBeInTheDocument();
  expect(store.getState().runtime.ownerSessionId).toBe("session");
  fireEvent.click(screen.getByRole("button", { name: "再试" }));
  await waitFor(() => expect(requests.filter((request) => request.path.endsWith("/retry"))).toEqual([
    { path: "/api/sessions/session/retry", method: "POST", body: { connection_id: "refreshed-connection" } },
  ]));
  await waitFor(() => expect(screen.queryByText(/执行已停止。供应商暂时不可用/)).toBeNull());
  expect(requests.filter((request) => request.method === "POST"
    && !request.path.endsWith("/select") && !request.path.endsWith("/retry"))).toEqual([]);
  expect(store.getState()[kondenApi.reducerPath].queries["getConversation(\"session\")"]?.data).toMatchObject({
    items: [{ kind: "user", message_id: "user", text: "原始请求" }],
  });
  cleanup();
  store.dispatch(kondenApi.util.resetApiState());
});

it("关闭操作结果只影响当前提示，同文案的新结果仍可关闭", async () => {
  prepareBrowser();
  const message = "Skill 已安装并启用。";
  const view: ConversationView = {
    session_id: "session", workspace_id: "workspace", revision: 1,
    compact_count: 0, compact_phase: null, waiting_until: null, work_plan: null, work_plan_updates: [], context_input_tokens: null,
    deleted: false, agent_name: null,
    session: {
      status: "waiting", waiting_for: ["external_fact"],
      should_wake: false, has_open_subagents: false, control_approval: null, control_message: message,
 last_error: null,
    },
    items: [{ kind: "user", message_id: "user", text: "安装 Skill", occurred_at: "2026-10-12T00:00:00Z", images: [], files: [] }],
  };
  const fetch = vi.fn(async () => Response.json(view));
  vi.stubGlobal("fetch", fetch);
  const store = configureStore({
    reducer: { runtime: runtimeReducer, [kondenApi.reducerPath]: kondenApi.reducer },
    middleware: (getDefaultMiddleware) => getDefaultMiddleware().concat(kondenApi.middleware),
  });
  store.dispatch(connected("connection"));
  render(<Provider store={store}><MantineProvider>
    <MemoryRouter initialEntries={["/sessions/session"]}>
      <Routes><Route path="/sessions/:sessionId" element={<Conversation />} /></Routes>
    </MemoryRouter>
  </MantineProvider></Provider>);
  const close = await screen.findByRole("button", { name: "关闭操作结果" });
  await waitFor(() => expect(store.getState().runtime.ownerSessionId).toBe("session"));
  fetch.mockClear();
  fireEvent.click(close);
  expect(screen.queryByText(message)).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
  act(() => {
    store.dispatch(kondenApi.util.updateQueryData("getConversation", "session", (draft) => { draft.revision += 1; }));
  });
  expect(screen.queryByText(message)).not.toBeInTheDocument();
  act(() => {
    store.dispatch(kondenApi.util.updateQueryData("getConversation", "session", (draft) => {
      draft.session.control_message = null;
      draft.session.control_approval = { request_id: "next", summary: "再次安装", risk: "需要确认" };
    }));
  });
  fireEvent.click(await screen.findByRole("button", { name: "确认" }));
  expect(await screen.findByRole("button", { name: "关闭操作结果" })).toBeVisible();
  cleanup();
  store.dispatch(kondenApi.util.resetApiState());
});
