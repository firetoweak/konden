import { MantineProvider } from "@mantine/core";
import { configureStore } from "@reduxjs/toolkit";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { Provider } from "react-redux";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { CapabilityInventory } from "../src/api/capabilities";
import { CapabilitiesPage } from "../src/features/capabilities/CapabilitiesPage";
import runtimeReducer, { connected } from "../src/realtime/runtimeSlice";

const state = vi.hoisted(() => ({
  data: undefined as CapabilityInventory | undefined,
  isError: false, isLoading: false,
  setEnabled: vi.fn(), refetch: vi.fn(), mutationError: undefined as unknown,
  testServer: vi.fn(), testError: undefined as unknown, isTesting: false,
}));
vi.mock("../src/api/kondenApi", () => ({
  useGetCapabilitiesQuery: () => ({ data: state.data, isLoading: state.isLoading, isError: state.isError, isFetching: false, refetch: state.refetch }),
  useSetCapabilityEnabledMutation: () => [state.setEnabled, { isLoading: false, error: state.mutationError }],
  useTestMcpServerMutation: () => [state.testServer, { isLoading: state.isTesting, error: state.testError }],
}));

beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  state.isError = false; state.isLoading = false; state.mutationError = undefined;
  state.isTesting = false; state.testError = undefined; state.testServer.mockClear();
  state.setEnabled.mockClear(); state.refetch.mockClear();
  state.data = {
    skills: [
      { id: "builtin:coding", name: "coding", description: "编码方法", origin: "builtin", enabled: true, source: "随 konden 提供", version: null, path: "/bundled/coding" },
      { id: "review", name: "review", description: "独立验证", origin: "installed", enabled: false, source: "/source", version: "v1", path: "/packages/review" },
    ],
    mcp: [{ id: "reports", name: "Reports", description: "报告查询", origin: "installed", enabled: true,
      transport: "streamable_http", read_only: true, last_status: "unknown", last_checked_at: null, last_error_summary: null }],
    cli: [{ id: "git", name: "git", description: "版本控制", source: "/git.json", version: "2.50.0", path: "/bin/git", health: null }],
  };
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

function app(live = true) {
  const store = configureStore({ reducer: { runtime: runtimeReducer } });
  if (live) store.dispatch(connected("web-connection"));
  return <Provider store={store}><MantineProvider env="test"><CapabilitiesPage /></MantineProvider></Provider>;
}

it("清单含停用项和内置项，搜索用途并展开来源，开关使用准确身份", () => {
  render(app());
  expect(screen.getByRole("button", { name: "查看 coding" })).toBeInTheDocument();
  expect(screen.getAllByRole("switch")).toHaveLength(1);
  const toggle = screen.getByRole("switch", { name: "启用 review" });
  expect(toggle).not.toBeChecked();
  fireEvent.click(toggle);
  expect(state.setEnabled).toHaveBeenCalledWith({ connectionId: "web-connection", kind: "skills", id: "review", enabled: true });
  fireEvent.change(screen.getByRole("textbox", { name: "搜索 Skill" }), { target: { value: "独立" } });
  expect(screen.queryByRole("button", { name: "查看 coding" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看 review" }));
  expect(screen.getByText("/source")).toBeVisible();
  expect(screen.getByText("/packages/review")).toBeVisible();
});

it("MCP开启仍显示未检测，CLI显示版本并且没有启停开关", () => {
  render(app());
  fireEvent.click(screen.getByRole("tab", { name: /MCP/ }));
  const list = within(screen.getByRole("list", { name: "MCP 清单" }));
  expect(list.getByText("未检测")).toBeVisible();
  expect(list.getByRole("switch", { name: "停用 Reports" })).toBeChecked();
  fireEvent.click(screen.getByRole("tab", { name: /CLI/ }));
  expect(screen.getByText("2.50.0")).toBeVisible();
  expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看 git" }));
  expect(screen.getByText("/bin/git")).toBeVisible();
});

it.each(["builtin", "installed"] as const)("%s MCP 可主动检测，检测中防止重复请求，失败原因可查看", (origin) => {
  state.data!.mcp[0] = { ...state.data!.mcp[0], origin, enabled: false };
  const view = render(app());
  fireEvent.click(screen.getByRole("tab", { name: /MCP/ }));
  fireEvent.click(screen.getByRole("button", { name: "检测 Reports 连接" }));
  expect(state.testServer).toHaveBeenCalledWith({ connectionId: "web-connection", id: "reports" });
  expect(state.setEnabled).not.toHaveBeenCalled();
  state.isTesting = true;
  view.rerender(app());
  expect(screen.getByRole("status")).toHaveTextContent("检测中");
  expect(screen.getByRole("button", { name: "检测 Reports 连接" })).toBeDisabled();
  state.isTesting = false;
  state.data!.mcp[0] = { ...state.data!.mcp[0], last_status: "unavailable", last_error_summary: "服务地址不可达" };
  view.rerender(app());
  expect(screen.getByRole("status")).toHaveTextContent("最近检测失败");
  expect(screen.getByRole("button", { name: "检测 Reports 连接" })).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "查看 Reports" }));
  expect(screen.getByText("服务地址不可达")).toBeVisible();
  expect(screen.getByText("已停用")).toBeVisible();
});

it("检测请求失败保留上次结果，浏览器断线时禁止新检测", () => {
  state.testError = "浏览器连接已失效，请重新连接。";
  const view = render(app());
  fireEvent.click(screen.getByRole("tab", { name: /MCP/ }));
  expect(screen.getByRole("alert")).toHaveTextContent("浏览器连接已失效");
  expect(screen.getByRole("status")).toHaveTextContent("未检测");
  view.rerender(app(false));
  expect(screen.getByRole("button", { name: "检测 Reports 连接" })).toBeDisabled();
});

it("区分读取失败、空清单与无搜索结果，并保留开关失败的实际状态", () => {
  state.mutationError = "浏览器连接已失效";
  const view = render(app());
  expect(screen.getByRole("alert")).toHaveTextContent("浏览器连接已失效");
  expect(screen.getByRole("switch", { name: "启用 review" })).not.toBeChecked();
  state.isError = true; state.data = undefined;
  view.rerender(app());
  expect(screen.getByRole("alert")).toHaveTextContent("能力清单加载失败");
  expect(screen.queryByText(/还没有/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重试" }));
  expect(state.refetch).toHaveBeenCalled();
  state.isError = false; state.data = { skills: [], mcp: [], cli: [] };
  view.rerender(app());
  expect(screen.getByRole("status")).toHaveTextContent("还没有已安装的 Skill");
  fireEvent.change(screen.getByRole("textbox", { name: "搜索 Skill" }), { target: { value: "missing" } });
  expect(screen.getByRole("status")).toHaveTextContent("没有找到匹配的能力");
});
