import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

const fetchMock = vi.fn();

function response(value: object, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function renderAt(path = "/") {
  window.history.replaceState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
  return render(<App />);
}

describe("project pages", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows loading and then the live project list", async () => {
    fetchMock.mockResolvedValueOnce(
      response([
        {
          id: "lobby",
          name: "Lobby",
          task_type: "reid",
          root: "/data/lobby",
          status: "reviewing",
        },
      ]),
    );
    renderAt();

    expect(screen.getByRole("status")).toHaveTextContent("正在读取项目");
    const project = await screen.findByRole("link", { name: /Lobby/ });
    expect(project).toHaveAttribute("href", "/projects/lobby");
    expect(project).toHaveTextContent("标注中");
  });

  it("shows an empty registry state", async () => {
    fetchMock.mockResolvedValueOnce(response([]));
    renderAt();
    expect(await screen.findByText("在 projects.yaml 中注册第一个项目")).toBeVisible();
  });

  it("shows backend errors without replacing them with an empty list", async () => {
    fetchMock.mockResolvedValueOnce(
      response(
        { detail: { code: "registry_invalid", message: "registry not found" } },
        500,
      ),
    );
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent("registry not found");
  });

  it("loads a project detail through its route", async () => {
    fetchMock.mockResolvedValueOnce(
      response({
        id: "lobby",
        name: "Lobby",
        task_type: "reid",
        root: "/data/lobby",
        status: "reviewing",
        summary: { pending: 3, labelled: 4 },
      }),
    );
    renderAt("/projects/lobby");
    expect(await screen.findByRole("heading", { name: "Lobby" })).toBeVisible();
    expect(screen.getByText("/data/lobby")).toBeVisible();
    expect(screen.getByText("pending")).toBeVisible();
    expect(screen.getByText("3")).toBeVisible();
  });

  it("distinguishes an unknown project from a service failure", async () => {
    fetchMock.mockResolvedValueOnce(
      response(
        { detail: { code: "project_not_found", message: "unknown project" } },
        404,
      ),
    );
    renderAt("/projects/missing");
    expect(await screen.findByRole("heading", { name: "项目不存在" })).toBeVisible();
    expect(screen.getByText(/missing/)).toBeVisible();
  });
});
