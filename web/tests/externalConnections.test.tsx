import { MantineProvider } from "@mantine/core";
import { configureStore } from "@reduxjs/toolkit";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { Provider } from "react-redux";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { ExternalConnection } from "../src/api/contracts";
import { kondenApi } from "../src/api/kondenApi";
import { ExternalAccess } from "../src/features/settings/ExternalAccess";
import runtimeReducer, { connected } from "../src/realtime/runtimeSlice";

beforeEach(() => {
  const NativeRequest = Request;
  vi.stubGlobal("Request", class extends NativeRequest {
    constructor(input: RequestInfo | URL, init?: RequestInit) {
      super(typeof input === "string" ? new URL(input, "http://localhost") : input, init);
    }
  });
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  Object.defineProperty(document, "fonts", { configurable: true, value: new EventTarget() });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); Reflect.deleteProperty(document, "fonts"); });

it("通过一次安装写入本机 Codex，再沿已有接入重装、改名和撤销", async () => {
  const store = configureStore({
    reducer: { runtime: runtimeReducer, [kondenApi.reducerPath]: kondenApi.reducer },
    middleware: (defaults) => defaults().concat(kondenApi.middleware),
  });
  store.dispatch(connected("human"));
  let item = { connection_id: "ab".repeat(16), name: "本机 Codex" };
  let entries: ExternalConnection[] = [];
  const installs: object[] = [];
  const removed = vi.fn();
  vi.stubGlobal("fetch", vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    expect(path).toMatch(/^\/api\/external-connections/);
    if (request.method === "GET") return Response.json(entries);
    if (path.endsWith("/codex")) {
      expect(request.method).toBe("PUT");
      installs.push(await request.json());
      entries = [item];
      return Response.json(item);
    }
    if (request.method === "PUT") {
      expect(await request.json()).toEqual({ connection_id: "human", name: "台式机 Codex" });
      item = { ...item, name: "台式机 Codex" };
      entries = [item];
      return Response.json(item);
    }
    expect(request.method).toBe("DELETE");
    expect(await request.json()).toEqual({ connection_id: "human" });
    expect(path.endsWith(item.connection_id)).toBe(true);
    removed();
    entries = [];
    return new Response(null, { status: 204 });
  }));
  render(<Provider store={store}><MantineProvider env="test"><ExternalAccess /></MantineProvider></Provider>);

  const first = await screen.findByRole("button", { name: "安装到本机 Codex" });
  expect(installs).toHaveLength(0);
  fireEvent.click(first);
  expect(await screen.findByRole("status")).toHaveTextContent("连上 Konden");
  expect(installs).toEqual([{ connection_id: "human" }]);

  fireEvent.click(await screen.findByRole("button", { name: "安装到 Codex 本机 Codex" }));
  await waitFor(() => expect(installs[1]).toEqual({ connection_id: "human", external_id: item.connection_id }));
  await screen.findByRole("status");
  fireEvent.click(screen.getByRole("button", { name: "重命名 本机 Codex" }));
  fireEvent.change(screen.getByLabelText("修改接入名称"), { target: { value: "台式机 Codex" } });
  fireEvent.click(screen.getByRole("button", { name: "保存名称" }));
  fireEvent.click(await screen.findByRole("button", { name: "撤销 台式机 Codex" }));
  fireEvent.click(screen.getByRole("button", { name: "取消" }));
  expect(removed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "撤销 台式机 Codex" }));
  fireEvent.click(screen.getByRole("button", { name: "确认撤销" }));
  expect(await screen.findByRole("button", { name: "安装到本机 Codex" })).toBeVisible();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  expect(removed).toHaveBeenCalledOnce();
});
