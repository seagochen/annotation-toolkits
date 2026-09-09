import { fireEvent, render, screen } from "@testing-library/react";
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
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "lobby",
          name: "Lobby",
          task_type: "reid",
          root: "/data/lobby",
          status: "reviewing",
          summary: { pending: 3, labelled: 4 },
        }),
      )
      .mockResolvedValueOnce(response({ actions: [], jobs: [] }));
    renderAt("/projects/lobby");
    expect(await screen.findByRole("heading", { name: "Lobby" })).toBeVisible();
    expect(screen.getByText("/data/lobby")).toBeVisible();
    expect(screen.getByText("pending")).toBeVisible();
    expect(screen.getByText("3")).toBeVisible();
  });

  it("starts a safe ReID action and shows persisted results", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "lobby",
          name: "Lobby",
          task_type: "reid",
          root: "/data/lobby",
          status: "reviewed",
          summary: {},
        }),
      )
      .mockResolvedValueOnce(
        response({
          actions: ["check", "train"],
          jobs: [
            {
              id: "old-job",
              name: "check",
              state: "done",
              created_at: "2026-09-06T00:00:00Z",
              started_at: "2026-09-06T00:00:00Z",
              finished_at: "2026-09-06T00:00:01Z",
              log: ["errors=0 warnings=0"],
              result: { exit_code: 0 },
              error: null,
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        response(
          {
            id: "new-job",
            name: "train",
            state: "queued",
            created_at: "2026-09-06T00:00:02Z",
            started_at: null,
            finished_at: null,
            log: [],
            result: null,
            error: null,
          },
          202,
        ),
      );

    renderAt("/projects/lobby");
    expect(await screen.findByText("errors=0 warnings=0")).toBeVisible();
    expect(screen.getByText('{"exit_code":0}')).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /启动训练/ }));
    expect(await screen.findByText("queued")).toBeVisible();

    const startRequest = fetchMock.mock.calls[2][0] as Request;
    await expect(startRequest.clone().json()).resolves.toEqual({
      options: { dry_run: true },
    });
  });

  it("reports an action conflict without hiding project details", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "lobby",
          name: "Lobby",
          task_type: "reid",
          root: "/data/lobby",
          status: "reviewed",
          summary: {},
        }),
      )
      .mockResolvedValueOnce(response({ actions: ["check"], jobs: [] }))
      .mockResolvedValueOnce(
        response(
          { detail: { code: "task_conflict", message: "another action is running" } },
          409,
        ),
      );

    renderAt("/projects/lobby");
    fireEvent.click(await screen.findByRole("button", { name: /检查冲突/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("another action is running");
    expect(screen.getByRole("heading", { name: "Lobby" })).toBeVisible();
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

  it("completes a ReID queue through the three-way review controls", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "lobby",
          name: "Lobby",
          task_type: "reid",
          root: "/data/lobby",
          status: "reviewing",
          summary: { pending: 1, labelled: 0 },
        }),
      )
      .mockResolvedValueOnce(
        response({
          total: 1,
          offset: 0,
          limit: 1,
          items: [
            {
              candidate_id: "c1",
              person_id1: "track-a",
              person_id2: "track-b",
              gallery1: ["images/a.jpg"],
              gallery2: ["images/b.jpg"],
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        response({
          item: { candidate_id: "c1", review_label: "same" },
          status: { state: "reviewed", details: { pending: 0 } },
        }),
      )
      .mockResolvedValueOnce(
        response({ total: 0, offset: 0, limit: 1, items: [] }),
      );

    renderAt("/projects/lobby/review");
    expect(await screen.findByText("track-a")).toBeVisible();
    expect(screen.getByText("1", { selector: ".queue-count strong" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("备注（可选）"), {
      target: { value: "same coat" },
    });
    fireEvent.click(screen.getByRole("button", { name: /同一人/ }));

    expect(await screen.findByRole("heading", { name: "候选队列已完成" })).toBeVisible();
    const submitRequest = fetchMock.mock.calls[2][0] as Request;
    expect(submitRequest.method).toBe("POST");
    await expect(submitRequest.clone().json()).resolves.toEqual({
      item_id: "c1",
      result: { label: "same", notes: "same coat" },
    });
  });

  it("keeps the candidate and retries the same decision after a save failure", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "lobby",
          name: "Lobby",
          task_type: "reid",
          root: "/data/lobby",
          status: "reviewing",
          summary: {},
        }),
      )
      .mockResolvedValueOnce(
        response({
          total: 1,
          offset: 0,
          limit: 1,
          items: [{ candidate_id: "c1", person_id1: "a", person_id2: "b" }],
        }),
      )
      .mockResolvedValueOnce(
        response(
          { detail: { code: "task_operation_error", message: "disk full" } },
          422,
        ),
      )
      .mockResolvedValueOnce(
        response({
          item: { candidate_id: "c1", review_label: "different" },
          status: { state: "reviewed", details: {} },
        }),
      )
      .mockResolvedValueOnce(
        response({ total: 0, offset: 0, limit: 1, items: [] }),
      );

    renderAt("/projects/lobby/review");
    fireEvent.click(await screen.findByRole("button", { name: /不同人/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("disk full");
    expect(screen.getByText("a")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "重试保存" }));

    expect(await screen.findByRole("heading", { name: "候选队列已完成" })).toBeVisible();
    const retryRequest = fetchMock.mock.calls[3][0] as Request;
    await expect(retryRequest.clone().json()).resolves.toMatchObject({
      result: { label: "different" },
    });
  });

  it("shows queue loading failures as errors", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "lobby",
          name: "Lobby",
          task_type: "reid",
          root: "/data/lobby",
          status: "reviewing",
          summary: {},
        }),
      )
      .mockResolvedValueOnce(
        response(
          { detail: { code: "registry_invalid", message: "queue unavailable" } },
          500,
        ),
      );
    renderAt("/projects/lobby/review");
    expect(await screen.findByRole("alert")).toHaveTextContent("queue unavailable");
  });

  it("completes a single-label classification queue", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "scenes",
          name: "Scenes",
          task_type: "classification",
          root: "/data/images",
          status: "reviewing",
          summary: { mode: "single", labels: ["indoor", "outdoor"] },
        }),
      )
      .mockResolvedValueOnce(
        response({
          total: 1,
          offset: 0,
          limit: 1,
          items: [{ item_id: "i1", image_path: "street.jpg", labels: [] }],
        }),
      )
      .mockResolvedValueOnce(
        response({
          item: { item_id: "i1", image_path: "street.jpg", labels: ["outdoor"] },
          status: { state: "reviewed", details: { pending: 0 } },
        }),
      )
      .mockResolvedValueOnce(
        response({ total: 0, offset: 0, limit: 1, items: [] }),
      );

    renderAt("/projects/scenes/classify");
    const outdoor = await screen.findByRole("radio", { name: "outdoor" });
    const submit = screen.getByRole("button", { name: "保存并继续" });
    expect(submit).toBeDisabled();
    fireEvent.click(outdoor);
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    expect(await screen.findByRole("heading", { name: "图像分类已完成" })).toBeVisible();
    const request = fetchMock.mock.calls[2][0] as Request;
    await expect(request.clone().json()).resolves.toEqual({
      item_id: "i1",
      result: { labels: ["outdoor"] },
    });
  });

  it("allows multiple labels and retains them when saving fails", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "objects",
          name: "Objects",
          task_type: "classification",
          root: "/data/images",
          status: "reviewing",
          summary: { mode: "multi", labels: ["person", "bag"] },
        }),
      )
      .mockResolvedValueOnce(
        response({
          total: 1,
          offset: 0,
          limit: 1,
          items: [{ item_id: "i2", image_path: "person.jpg", labels: [] }],
        }),
      )
      .mockResolvedValueOnce(
        response(
          { detail: { code: "task_operation_error", message: "disk full" } },
          422,
        ),
      );

    renderAt("/projects/objects/classify");
    const person = await screen.findByRole("checkbox", { name: "person" });
    const bag = screen.getByRole("checkbox", { name: "bag" });
    fireEvent.click(person);
    fireEvent.click(bag);
    fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("disk full");
    expect(person).toBeChecked();
    expect(bag).toBeChecked();
    expect(screen.getByRole("button", { name: "重试保存" })).toBeVisible();
  });

  it("completes an image captioning queue", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "scenes",
          name: "Scenes",
          task_type: "caption",
          root: "/data/images",
          status: "reviewing",
          summary: {},
        }),
      )
      .mockResolvedValueOnce(
        response({
          total: 1,
          offset: 0,
          limit: 1,
          items: [{ item_id: "i1", image_path: "street.jpg", caption: "" }],
        }),
      )
      .mockResolvedValueOnce(
        response({
          item: { item_id: "i1", image_path: "street.jpg", caption: "A busy street." },
          status: { state: "reviewed", details: { pending: 0 } },
        }),
      )
      .mockResolvedValueOnce(
        response({ total: 0, offset: 0, limit: 1, items: [] }),
      );

    renderAt("/projects/scenes/caption");
    const textarea = await screen.findByLabelText("图像描述");
    const submit = screen.getByRole("button", { name: "保存并继续" });
    expect(submit).toBeDisabled();
    fireEvent.change(textarea, { target: { value: "  A busy street.  " } });
    expect(submit).toBeEnabled();
    fireEvent.click(submit);

    expect(await screen.findByRole("heading", { name: "图像描述已完成" })).toBeVisible();
    const request = fetchMock.mock.calls[2][0] as Request;
    await expect(request.clone().json()).resolves.toEqual({
      item_id: "i1",
      result: { caption: "A busy street." },
    });
  });
});
