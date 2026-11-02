import { MantineProvider } from "@mantine/core";
import { configureStore } from "@reduxjs/toolkit";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Provider } from "react-redux";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { kondenApi } from "../src/api/kondenApi";
import { FileAccessPanel } from "../src/features/conversation/FileAccessPanel";
import type { FileAccess } from "../src/api/contracts";
import { openEventBridge } from "../src/realtime/eventBridge";

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  const NativeRequest = Request;
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("另一页面保存或连接恢复后重新读取当前访问范围", async () => {
  let current: FileAccess = { mode: "directories", directories: [] };
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(current)));
  const listeners = new Map<string, (event: MessageEvent) => void>();
  vi.stubGlobal("EventSource", class {
    addEventListener(name: string, listener: (event: MessageEvent) => void) { listeners.set(name, listener); }
    close() {}
  });
  const store = configureStore({
    reducer: { [kondenApi.reducerPath]: kondenApi.reducer },
    middleware: (defaults) => defaults().concat(kondenApi.middleware),
  });
  const query = store.dispatch(kondenApi.endpoints.getFileAccess.initiate("bot"));
  await query.unwrap();
  const close = openEventBridge(store.dispatch);
  const emit = (name: string, payload: object) => listeners.get(name)!(new MessageEvent(name, { data: JSON.stringify(payload) }));
  current = { mode: "full" };
  emit("file_access_changed", { session_id: "bot" });
  await waitFor(() => expect(kondenApi.endpoints.getFileAccess.select("bot")(store.getState()).data).toEqual(current));
  current = { mode: "directories", directories: ["E:/notes"] };
  emit("connected", { connection_id: "fresh-connection" });
  await waitFor(() => expect(kondenApi.endpoints.getFileAccess.select("bot")(store.getState()).data).toEqual(current));
  close();
  query.unsubscribe();
  store.dispatch(kondenApi.util.resetApiState());
});

it("保存冲突保留访问范围草稿，重试成功后显示后端确认值", async () => {
  let current: FileAccess = { mode: "directories", directories: [] };
  let blocked = true;
  const updates: unknown[] = [];
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    if (request.method === "PUT") {
      const body = await request.json();
      updates.push(body);
      if (blocked) return Response.json({ detail: "请先停止后台命令" }, { status: 409 });
      current = body.config;
    }
    return Response.json(current);
  }));
  const store = configureStore({
    reducer: { [kondenApi.reducerPath]: kondenApi.reducer },
    middleware: (defaults) => defaults().concat(kondenApi.middleware),
  });
  render(<Provider store={store}><MantineProvider env="test">
    <FileAccessPanel sessionId="bot" connectionId="connection" active />
  </MantineProvider></Provider>);
  fireEvent.click(await screen.findByRole("button", { name: "添加目录" }));
  fireEvent.change(screen.getByRole("textbox", { name: "访问目录 1" }), { target: { value: "E:/notes" } });
  fireEvent.click(screen.getByRole("radio", { name: "全部目录" }));
  expect(screen.queryByRole("textbox", { name: "访问目录 1" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("radio", { name: "指定目录" }));
  expect(screen.getByRole("textbox", { name: "访问目录 1" })).toHaveValue("E:/notes");
  fireEvent.click(screen.getByRole("button", { name: "保存可用目录" }));
  expect(await screen.findByText("请先停止后台命令")).toBeVisible();
  expect(screen.getByRole("textbox", { name: "访问目录 1" })).toHaveValue("E:/notes");
  blocked = false;
  fireEvent.click(screen.getByRole("button", { name: "保存可用目录" }));
  expect(await screen.findByText("已保存")).toBeVisible();
  await waitFor(() => expect(screen.getByRole("button", { name: "保存可用目录" })).toBeDisabled());
  expect(updates).toEqual(Array(2).fill({ connection_id: "connection", config: { mode: "directories", directories: ["E:/notes"] } }));
  store.dispatch(kondenApi.util.resetApiState());
});
