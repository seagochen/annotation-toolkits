import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

// Page requests, answered in order by each test. The side navigation's
// project-list request and the workspace's image-list request (the queue
// without a status filter) are answered separately (see `projectList` and
// `itemList`) so that they do not shift the order every test relies on.
const fetchMock = vi.fn();
let projectList: () => Response;
let itemList: () => Response;

function routedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const request = input instanceof Request ? input : new Request(input, init);
  const url = new URL(request.url);
  if (url.pathname.startsWith("/api/legacy-history/") || url.pathname.endsWith("/ai-jobs")) {
    return Promise.resolve(response({}, 404));
  }
  if (request.method === "GET" && url.pathname === "/api/projects") {
    return Promise.resolve(projectList());
  }
  if (request.method === "GET" && url.pathname.endsWith("/queue") && !url.searchParams.has("status")) {
    return Promise.resolve(itemList());
  }
  return fetchMock(request);
}

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


const classificationType = {
  type: "classification",
  label: "图像分类",
  description: "为每张图像选择标签。",
  import_modes: ["upload", "directory"],
  upload_extensions: [".jpeg", ".jpg", ".md", ".png", ".txt", ".webp"],
  export_formats: [{ format: "native", label: "原生 JSON", contract: "classification-json/v1" }],
  fields: [
    {
      key: "labels",
      label: "标签",
      type: "list",
      required: true,
      default: null,
      options: null,
      help: null,
      lock: "append_only",
      server_only: false,
      group: null,
    },
    {
      key: "mode",
      label: "选择方式",
      type: "select",
      required: true,
      default: "single",
      options: [
        { value: "single", label: "单选" },
        { value: "multi", label: "多选" },
      ],
      help: null,
      lock: "locked",
      server_only: false,
      group: null,
    },
  ],
};

const streetProject = {
  id: "street",
  name: "Street",
  task_type: "classification",
  root: "/workspace/projects/street/data",
  status: "reviewing",
  summary: { labels: ["indoor", "outdoor"], mode: "single", total: 2, labelled: 1, pending: 1 },
};

function streetSettings(overrides: Record<string, unknown> = {}) {
  return {
    id: "street",
    name: "Street",
    task_type: "classification",
    values: { labels: ["indoor", "outdoor"], mode: "single" },
    fields: classificationType.fields,
    config_path: "/workspace/projects/street/config.yaml",
    config_text: "dataset: ./data\nlabels: [indoor, outdoor]\nmode: single\n",
    data_source: { mode: "managed", path: "/workspace/projects/street/data" },
    import_roots: ["/workspace"],
    annotated: true,
    ...overrides,
  };
}

describe("project pages", () => {
  beforeEach(() => {
    fetchMock.mockReset();
    projectList = () => response([]);
    itemList = () => response({ total: 0, offset: 0, limit: 200, items: [] });
    vi.stubGlobal("fetch", routedFetch);
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("lists projects in the side navigation and offers a new project on the home page", async () => {
    projectList = () =>
      response([
        {
          id: "lobby",
          name: "Lobby",
          task_type: "reid",
          root: "/data/lobby",
          status: "reviewing",
        },
      ]);
    renderAt();

    const navigation = screen.getByRole("navigation", { name: "项目" });
    const project = await within(navigation).findByRole("link", { name: /Lobby/ });
    expect(project).toHaveAttribute("href", "/projects/lobby");
    expect(screen.getByRole("heading", { name: "选择或新建一个项目" })).toBeVisible();
    expect(screen.getAllByRole("button", { name: /新建项目/ })).toHaveLength(2);
  });

  it("shows an empty workspace", async () => {
    renderAt();
    expect(await screen.findByRole("heading", { name: "创建第一个标注项目" })).toBeVisible();
    expect(await screen.findByText("还没有项目")).toBeVisible();
  });

  it("shows backend errors without replacing them with an empty list", async () => {
    projectList = () =>
      response({ detail: { code: "registry_invalid", message: "registry not found" } }, 500);
    renderAt();
    expect(await screen.findByText("registry not found")).toBeVisible();
    expect(screen.queryByText("还没有项目")).toBeNull();
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

    expect(await screen.findByRole("heading", { name: "分类已完成" })).toBeVisible();
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
          task_type: "captioning",
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

    expect(await screen.findByRole("heading", { name: "描述 / 文本生成已完成" })).toBeVisible();
    const request = fetchMock.mock.calls[2][0] as Request;
    await expect(request.clone().json()).resolves.toEqual({
      item_id: "i1",
      result: { caption: "A busy street." },
    });
  });

  it("shows text documents for classification and generation, and explains unreadable ones", async () => {
    const document = "第一段。\n\n第二段很长。";
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "reviews",
          name: "Reviews",
          task_type: "classification",
          root: "/data/reviews",
          status: "reviewing",
          summary: { mode: "single", labels: ["positive", "negative"] },
        }),
      )
      .mockResolvedValueOnce(
        response({
          total: 2,
          offset: 0,
          limit: 1,
          items: [
            { item_id: "t1", image_path: "a.txt", media: "text", text: document, text_error: null, labels: [] },
          ],
        }),
      )
      .mockResolvedValueOnce(
        response({
          item: { item_id: "t1", image_path: "a.txt", labels: ["positive"] },
          status: { state: "reviewing", details: { pending: 1 } },
        }),
      )
      .mockResolvedValueOnce(
        response({
          total: 1,
          offset: 0,
          limit: 1,
          items: [
            {
              item_id: "t2",
              image_path: "b.txt",
              media: "text",
              text: null,
              text_error: "classification document 'b.txt' is not valid UTF-8 (byte 3)",
              labels: [],
            },
          ],
        }),
      );

    renderAt("/projects/reviews/classify");
    const article = await screen.findByRole("article", { name: "文本：a.txt" });
    expect(article.textContent).toBe(document);
    expect(screen.getByRole("heading", { name: "文本分类" })).toBeVisible();
    expect(screen.queryByRole("img")).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "positive" }));
    fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("not valid UTF-8");
    expect(screen.queryByRole("article")).toBeNull();
  });

  it("allows long generated text for a document", async () => {
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "docs",
          name: "Docs",
          task_type: "captioning",
          root: "/data/docs",
          status: "reviewing",
          summary: {},
        }),
      )
      .mockResolvedValueOnce(
        response({
          total: 1,
          offset: 0,
          limit: 1,
          items: [{ item_id: "d1", image_path: "a.md", media: "text", text: "# Title", text_error: null, caption: "" }],
        }),
      );

    renderAt("/projects/docs/caption");
    const textarea = await screen.findByLabelText("生成文本");
    expect(textarea).toHaveAttribute("maxLength", "20000");
    expect(screen.getByRole("article", { name: "文本：a.md" })).toHaveTextContent("# Title");
    expect(screen.getByRole("heading", { name: "文本生成" })).toBeVisible();
  });

  it("creates overlapping text spans from DOM selections and submits code point offsets", async () => {
    const text = "🎉张三在北京。";
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "news",
          name: "News",
          task_type: "text_span",
          root: "/data/news",
          status: "reviewing",
          summary: { labels: ["PER", "LOC"], total: 1, annotated: 0, pending: 1 },
        }),
      )
      .mockResolvedValueOnce(
        response({
          total: 1,
          offset: 0,
          limit: 1,
          items: [{ item_id: "n1", image_path: "a.txt", media: "text", text, text_error: null, spans: [] }],
        }),
      )
      .mockResolvedValueOnce(
        response({
          item: { item_id: "n1", image_path: "a.txt", length: 7, spans: [] },
          status: { state: "reviewed", details: { pending: 0 } },
        }),
      )
      .mockResolvedValueOnce(response({ total: 0, offset: 0, limit: 1, items: [] }));

    renderAt("/projects/news/spans");
    const article = await screen.findByRole("article", { name: "文本：a.txt" });
    expect(article.textContent).toBe(text);

    function select(from: number, to: number) {
      // Offsets are UTF-16 positions in the rendered text, as a mouse drag gives.
      const walker = document.createTreeWalker(article, NodeFilter.SHOW_TEXT);
      const nodes: Text[] = [];
      while (walker.nextNode()) nodes.push(walker.currentNode as Text);
      const locate = (offset: number): [Text, number] => {
        for (const node of nodes) {
          if (offset <= node.length) return [node, offset];
          offset -= node.length;
        }
        throw new Error("offset out of range");
      };
      const range = document.createRange();
      range.setStart(...locate(from));
      range.setEnd(...locate(to));
      const selection = window.getSelection()!;
      selection.removeAllRanges();
      selection.addRange(range);
      fireEvent.mouseUp(article.firstElementChild!);
    }

    // "张三在北京" with the selection catching the emoji's neighbour only.
    select(2, 7);
    expect(screen.getByRole("button", { name: /PER：张三在北京/ })).toBeVisible();
    // A nested span with another label: deselect, choose LOC, select "北京".
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("radio", { name: "LOC" }));
    expect(screen.getByRole("button", { name: /PER：张三在北京/ })).toBeVisible();
    select(5, 7);
    expect(screen.getByRole("button", { name: /LOC：北京/ })).toBeVisible();
    expect(article.querySelectorAll("mark").length).toBe(2);
    // Relabel the selected (LOC) span to PER with the digit key, then back.
    fireEvent.keyDown(window, { key: "1" });
    expect(screen.getByRole("button", { name: /PER：北京/ })).toBeVisible();
    fireEvent.keyDown(window, { key: "2" });
    fireEvent.click(screen.getByRole("button", { name: "保存并继续" }));

    expect(await screen.findByRole("heading", { name: "文本片段标注已完成" })).toBeVisible();
    const request = fetchMock.mock.calls[2][0] as Request;
    await expect(request.clone().json()).resolves.toEqual({
      item_id: "n1",
      result: {
        spans: [
          { start: 1, end: 6, label: "PER" },
          { start: 4, end: 6, label: "LOC" },
        ],
      },
    });
  });

  it("loads image dimensions and submits an empty-box detection result", async () => {
    class InstantImage {
      onload: (() => void) | null = null;
      naturalWidth = 100;
      naturalHeight = 50;
      set src(_value: string) {
        queueMicrotask(() => this.onload?.());
      }
    }
    vi.stubGlobal("Image", InstantImage);
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue(null);

    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "yard",
          name: "Yard",
          task_type: "detection",
          root: "/data/images",
          status: "reviewing",
          summary: { categories: ["cat", "dog"] },
        }),
      )
      .mockResolvedValueOnce(
        response({
          total: 1,
          offset: 0,
          limit: 1,
          items: [{ item_id: "i1", image_path: "yard.jpg", boxes: [] }],
        }),
      )
      .mockResolvedValueOnce(
        response({
          item: { item_id: "i1", image_path: "yard.jpg", image_size: { width: 100, height: 50 }, boxes: [] },
          status: { state: "reviewed", details: { pending: 0 } },
        }),
      )
      .mockResolvedValueOnce(
        response({ total: 0, offset: 0, limit: 1, items: [] }),
      );

    renderAt("/projects/yard/detect");
    expect(await screen.findByRole("radio", { name: "cat" })).toBeVisible();
    expect(screen.getByRole("application", { name: "yard.jpg" })).toBeVisible();
    const save = screen.getByRole("button", { name: "保存并继续" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    expect(await screen.findByRole("heading", { name: "目标检测已完成" })).toBeVisible();
    const request = fetchMock.mock.calls[2][0] as Request;
    await expect(request.clone().json()).resolves.toEqual({
      item_id: "i1",
      result: { image_size: { width: 100, height: 50 }, boxes: [] },
    });
    getContext.mockRestore();
  });

  it("lists the project's images beside the canvas and opens a pending one from the list", async () => {
    class InstantImage {
      onload: (() => void) | null = null;
      naturalWidth = 100;
      naturalHeight = 50;
      set src(_value: string) {
        queueMicrotask(() => this.onload?.());
      }
    }
    vi.stubGlobal("Image", InstantImage);
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    itemList = () =>
      response({
        total: 3,
        offset: 0,
        limit: 200,
        items: [
          { item_id: "a", image_path: "a.jpg", boxes: [], annotated: true },
          { item_id: "b", image_path: "b.jpg", boxes: [], annotated: false },
          { item_id: "c", image_path: "c.jpg", boxes: [], annotated: false },
        ],
      });
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "yard",
          name: "Yard",
          task_type: "detection",
          root: "/data/images",
          status: "reviewing",
          summary: { categories: ["cat"], total: 3, pending: 2 },
        }),
      )
      .mockResolvedValueOnce(
        response({ total: 2, offset: 0, limit: 1, items: [{ item_id: "b", image_path: "b.jpg", boxes: [] }] }),
      )
      .mockResolvedValueOnce(
        response({ total: 2, offset: 1, limit: 1, items: [{ item_id: "c", image_path: "c.jpg", boxes: [] }] }),
      );

    renderAt("/projects/yard/detect");
    const strip = await screen.findByRole("complementary", { name: "图像列表" });
    expect(await within(strip).findByText("2 / 3")).toBeVisible();
    // Detection results cannot be revised, so a finished image is ticked but not openable.
    expect(within(strip).getByRole("button", { name: "a.jpg（已标注）" })).toBeDisabled();
    expect(within(strip).getByRole("button", { name: "b.jpg" })).toHaveAttribute("aria-current", "true");

    fireEvent.click(within(strip).getByRole("button", { name: "下一张" }));
    expect(await within(strip).findByText("3 / 3")).toBeVisible();
    const browse = new URL((fetchMock.mock.calls[2][0] as Request).url);
    expect(browse.searchParams.get("status")).toBe("pending");
    expect(browse.searchParams.get("offset")).toBe("1");
    expect(screen.getByRole("application", { name: "c.jpg" })).toBeVisible();
    expect(within(strip).getByRole("button", { name: "下一张" })).toBeDisabled();
    getContext.mockRestore();
  });

  it.each(["polygon", "segmentation"])("confirms before discarding an unfinished %s polygon on image navigation", async (taskType) => {
    class InstantImage {
      onload: (() => void) | null = null;
      naturalWidth = 100;
      naturalHeight = 80;
      set src(_value: string) { queueMicrotask(() => this.onload?.()); }
    }
    vi.stubGlobal("Image", InstantImage);
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const items = [
      { item_id: "p1", image_path: "a.jpg", annotated: false, polygons: [], source: "none", revision: 0 },
      { item_id: "p2", image_path: "b.jpg", annotated: false, polygons: [], source: "none", revision: 0 },
    ];
    itemList = () => response({ total: 2, offset: 0, limit: 200, items });
    fetchMock
      .mockResolvedValueOnce(response({ id: "drafts", name: "Drafts", task_type: taskType, summary: { categories: ["cat"], total: 2, pending: 2 } }))
      .mockResolvedValueOnce(response({ total: 2, offset: 0, limit: 1, items: [items[0]] }))
      .mockResolvedValueOnce(response({ total: 2, offset: 1, limit: 1, items: [items[1]] }));
    renderAt(`/projects/drafts/${taskType === "segmentation" ? "segment" : "polygon"}`);
    const canvas = await screen.findByRole("application", { name: "a.jpg" });
    fireEvent.keyDown(window, { key: "p" });
    const pointer = new MouseEvent("pointerdown", { bubbles: true, clientX: 30, clientY: 30 });
    Object.defineProperty(pointer, "pointerId", { value: 1 });
    fireEvent(canvas, pointer);
    const target = await screen.findByRole("button", { name: "b.jpg" });
    fireEvent.click(target);
    expect(confirm).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("application", { name: "a.jpg" })).toBeVisible();
    confirm.mockReturnValue(true);
    fireEvent.click(target);
    expect(await screen.findByRole("application", { name: "b.jpg" })).toBeVisible();
  });

  it("starts a polygon item from its prelabel, edits it and submits the revision it started from", async () => {
    class InstantImage {
      onload: (() => void) | null = null;
      naturalWidth = 100;
      naturalHeight = 80;
      set src(_value: string) {
        queueMicrotask(() => this.onload?.());
      }
    }
    vi.stubGlobal("Image", InstantImage);
    const getContext = vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const prelabelItem = {
      item_id: "p1",
      image_path: "wall.jpg",
      revision: 0,
      source: "prelabel",
      image_size: { width: 100, height: 80 },
      polygons: [
        { category: "material", points: [[10, 10], [50, 10], [50, 50], [10, 50]] },
        { category: "crack", points: [[60, 10], [90, 10], [75, 40]] },
      ],
    };
    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "walls",
          name: "Walls",
          task_type: "polygon",
          root: "/data/walls",
          status: "reviewing",
          summary: { categories: ["material", "crack"], total: 2, annotated: 1, pending: 1 },
        }),
      )
      .mockResolvedValueOnce(response({ total: 1, offset: 0, limit: 1, items: [prelabelItem] }))
      .mockResolvedValueOnce(
        response({ detail: { code: "task_conflict", message: "polygon item 'p1' is at revision 1" } }, 409),
      )
      .mockResolvedValueOnce(
        response({
          total: 1,
          offset: 0,
          limit: 1,
          items: [{ ...prelabelItem, revision: 2, source: "annotation", polygons: [] }],
        }),
      );

    renderAt("/projects/walls/polygon");
    expect(await screen.findByText("初始内容：COCO 预标。")).toBeVisible();
    // The shapes are listed under the "图层" tab of the annotation panel.
    fireEvent.click(screen.getByRole("button", { name: "图层" }));
    expect(screen.getByRole("button", { name: /#1 material/ })).toBeVisible();
    // Select the first polygon from the list, relabel it with the digit key, delete the second.
    fireEvent.click(screen.getByRole("button", { name: /#1 material/ }));
    fireEvent.keyDown(window, { key: "2" });
    expect(screen.getByRole("button", { name: /#1 crack/ })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "删除多边形 #2" }));
    const save = screen.getByRole("button", { name: "保存并继续" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    expect(await screen.findByRole("alert")).toHaveTextContent("at revision 1");
    const request = fetchMock.mock.calls[2][0] as Request;
    await expect(request.clone().json()).resolves.toEqual({
      item_id: "p1",
      result: {
        image_size: { width: 100, height: 80 },
        base_revision: 0,
        polygons: [{ category: "crack", points: [[10, 10], [50, 10], [50, 50], [10, 50]] }],
      },
    });

    // Browsing submitted results asks for the annotated queue.
    fireEvent.click(screen.getByRole("button", { name: "已提交" }));
    expect(await screen.findByText(/第 2 版/)).toBeVisible();
    const browse = new URL((fetchMock.mock.calls[3][0] as Request).url);
    expect(browse.searchParams.get("status")).toBe("annotated");
    expect(browse.searchParams.get("offset")).toBe("0");
    expect(screen.getByText("还没有多边形。该图没有目标时可直接保存。")).toBeVisible();
    getContext.mockRestore();
  });

  it("shows the versioned contract of each export format", async () => {
    fetchMock.mockResolvedValueOnce(response(streetProject)).mockResolvedValueOnce(
      response([
        {
          ...classificationType,
          export_formats: [
            { format: "native", label: "原生 JSON", contract: "classification-json/v1" },
            { format: "csv", label: "CSV", contract: "classification-csv/v1" },
          ],
        },
      ]),
    );
    renderAt("/projects/street/export");
    const formats = await screen.findByRole("radiogroup", { name: "导出格式" });
    expect(within(formats).getByText("classification-json/v1")).toBeVisible();
    expect(within(formats).getByText("classification-csv/v1")).toBeVisible();
  });

  it("lists every prelabel entry that was not loaded on the project overview", async () => {
    fetchMock.mockResolvedValueOnce(
      response({
        id: "walls",
        name: "Walls",
        task_type: "polygon",
        root: "/data/walls",
        status: "reviewing",
        summary: {
          categories: ["material"],
          prelabels: "/data/walls/prelabels.coco.json",
          total: 3,
          annotated: 0,
          pending: 3,
          prelabel_images: 1,
          prelabel_issue_count: 2,
          prelabel_issues: [
            { file_name: "c.jpg", image_id: 3, annotation_id: 32, reason: "RLE segmentation is not supported" },
            { file_name: null, image_id: 404, annotation_id: 99, reason: "annotation does not refer to an `images` entry" },
          ],
        },
      }),
    );
    renderAt("/projects/walls");
    const card = await screen.findByRole("article", { name: "未加载的预标" });
    expect(within(card).getByRole("heading", { name: "未加载的预标（2 条）" })).toBeVisible();
    expect(within(card).getAllByRole("row")).toHaveLength(3);
    expect(within(card).getByText("RLE segmentation is not supported")).toBeVisible();
    expect(screen.getByText("未加载的预标条目")).toBeVisible();
  });

  it("submits a blank segmentation mask matching the loaded image size", async () => {
    class InstantImage {
      onload: (() => void) | null = null;
      naturalWidth = 2;
      naturalHeight = 2;
      set src(_value: string) {
        queueMicrotask(() => this.onload?.());
      }
    }
    vi.stubGlobal("Image", InstantImage);
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue(null);

    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "roads",
          name: "Roads",
          task_type: "segmentation",
          root: "/data/images",
          status: "reviewing",
          summary: { categories: ["road", "building"] },
        }),
      )
      .mockResolvedValueOnce(
        response({
          total: 1,
          offset: 0,
          limit: 1,
          items: [{ item_id: "i1", image_path: "tile.png", mask_path: null }],
        }),
      )
      .mockResolvedValueOnce(
        response({
          item: { item_id: "i1", image_path: "tile.png", mask_path: "i1.png" },
          status: { state: "reviewed", details: { pending: 0 } },
        }),
      )
      .mockResolvedValueOnce(
        response({ total: 0, offset: 0, limit: 1, items: [] }),
      );

    renderAt("/projects/roads/segment");
    expect(await screen.findByRole("radio", { name: "road" })).toBeVisible();
    const submit = await screen.findByRole("button", { name: "保存并继续" });
    fireEvent.click(submit);

    expect(await screen.findByRole("heading", { name: "图像分割已完成" })).toBeVisible();
    const request = fetchMock.mock.calls[2][0] as Request;
    await expect(request.clone().json()).resolves.toEqual({
      item_id: "i1",
      result: { image_size: { width: 2, height: 2 }, pixels: "AAAAAA==" },
    });
    getContext.mockRestore();
  });

  it("starts from a blank depth raster when no baseline is available", async () => {
    class InstantImage {
      onload: (() => void) | null = null;
      naturalWidth = 2;
      naturalHeight = 2;
      set src(_value: string) {
        queueMicrotask(() => this.onload?.());
      }
    }
    vi.stubGlobal("Image", InstantImage);
    const getContext = vi
      .spyOn(HTMLCanvasElement.prototype, "getContext")
      .mockReturnValue(null);

    fetchMock
      .mockResolvedValueOnce(
        response({
          id: "tunnel",
          name: "Tunnel",
          task_type: "depth",
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
          items: [{ item_id: "i1", image_path: "tile.png", baseline_path: null, depth_path: null }],
        }),
      )
      .mockResolvedValueOnce(
        response({
          item: { item_id: "i1", image_path: "tile.png", depth_path: "i1.png" },
          status: { state: "reviewed", details: { pending: 0 } },
        }),
      )
      .mockResolvedValueOnce(
        response({ total: 0, offset: 0, limit: 1, items: [] }),
      );

    renderAt("/projects/tunnel/depth");
    expect(await screen.findByText("未找到基线深度图，已从中灰度（128）开始编辑。")).toBeVisible();
    // The button enables once the blank raster exists, a render after the hint.
    const save = screen.getByRole("button", { name: "保存并继续" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);

    expect(await screen.findByRole("heading", { name: "深度图标注已完成" })).toBeVisible();
    const request = fetchMock.mock.calls[2][0] as Request;
    await expect(request.clone().json()).resolves.toEqual({
      item_id: "i1",
      result: { image_size: { width: 2, height: 2 }, pixels: "gICAgA==" },
    });
    getContext.mockRestore();
  });

  it("creates a project from the task-type menu and the property page", async () => {
    fetchMock
      .mockResolvedValueOnce(response([classificationType]))
      .mockResolvedValueOnce(response([classificationType]))
      .mockResolvedValueOnce(response({ ...streetProject, status: "empty", summary: {} }, 201))
      .mockResolvedValueOnce(response({ ...streetProject, status: "empty", summary: { total: 0, pending: 0 } }));
    renderAt();

    fireEvent.click(screen.getAllByRole("button", { name: /新建项目/ })[0]);
    const dialog = await screen.findByRole("dialog", { name: "新建项目" });
    fireEvent.click(await within(dialog).findByRole("radio", { name: /图像分类/ }));
    fireEvent.click(within(dialog).getByRole("button", { name: "下一步" }));

    const create = await screen.findByRole("button", { name: "创建项目" });
    expect(create).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^项目名称/), { target: { value: " Street " } });
    const labels = screen.getByLabelText(/^标签/);
    fireEvent.change(labels, { target: { value: "indoor, outdoor" } });
    fireEvent.keyDown(labels, { key: "Enter" });
    expect(screen.getByRole("button", { name: "删除 indoor" })).toBeVisible();
    fireEvent.click(create);

    expect(await screen.findByRole("heading", { name: "Street" })).toBeVisible();
    const request = fetchMock.mock.calls[2][0] as Request;
    expect(request.method).toBe("POST");
    await expect(request.clone().json()).resolves.toEqual({
      name: "Street",
      task_type: "classification",
      settings: { labels: ["indoor", "outdoor"], mode: "single" },
    });
    expect(screen.getByRole("link", { name: /导入数据/ })).toHaveClass("primary");
  });

  it("edits the category list with confirm, reorder, rename and delete", async () => {
    fetchMock
      .mockResolvedValueOnce(response([classificationType]))
      .mockResolvedValueOnce(response({ ...streetProject, status: "empty", summary: {} }, 201))
      .mockResolvedValueOnce(response({ ...streetProject, status: "empty", summary: {} }));
    renderAt("/new/classification");

    const input = await screen.findByLabelText(/^标签/);
    const confirm = screen.getByRole("button", { name: "确认" });
    fireEvent.change(screen.getByLabelText(/^项目名称/), { target: { value: "Street" } });
    for (const name of ["ゴミ範囲", "灰の範囲", "背景"]) {
      fireEvent.change(input, { target: { value: name } });
      fireEvent.click(confirm);
    }
    fireEvent.change(input, { target: { value: "背景" } });
    fireEvent.click(confirm);
    expect(screen.getByRole("alert")).toHaveTextContent("已存在");
    fireEvent.change(input, { target: { value: "" } });

    // Move 背景 to the front with the keyboard, then delete 灰の範囲.
    const handle = screen.getByRole("button", { name: /调整 背景 的顺序/ });
    fireEvent.keyDown(handle, { key: "ArrowUp" });
    fireEvent.keyDown(screen.getByRole("button", { name: /调整 背景 的顺序/ }), { key: "ArrowUp" });
    fireEvent.click(screen.getByRole("button", { name: "删除 灰の範囲" }));

    // Rename ゴミ範囲; a name that already exists is refused.
    fireEvent.click(screen.getByRole("button", { name: "修改 ゴミ範囲" }));
    const edit = screen.getByRole("textbox", { name: "修改 ゴミ範囲 的名称" });
    fireEvent.change(edit, { target: { value: "背景" } });
    fireEvent.keyDown(edit, { key: "Enter" });
    expect(screen.getByRole("alert")).toHaveTextContent("已存在");
    fireEvent.change(edit, { target: { value: "ゴミ" } });
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));

    const entries = within(screen.getByRole("list", { name: "已添加" })).getAllByRole("listitem");
    expect(entries.map((entry) => entry.textContent)).toEqual(["1.背景", "2.ゴミ"]);

    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));
    expect(await screen.findByRole("heading", { name: "Street" })).toBeVisible();
    const request = fetchMock.mock.calls[1][0] as Request;
    await expect(request.clone().json()).resolves.toMatchObject({
      settings: { labels: ["背景", "ゴミ"] },
    });
  });

  it("keeps existing labels fixed once a project has annotations", async () => {
    fetchMock
      .mockResolvedValueOnce(response(streetProject))
      .mockResolvedValueOnce(response(streetSettings()))
      .mockResolvedValueOnce(
        response(streetSettings({ values: { labels: ["indoor", "outdoor", "night"], mode: "single" } })),
      )
      .mockResolvedValueOnce(response(streetProject));
    renderAt("/projects/street/settings");

    expect(await screen.findByText("indoor")).toBeVisible();
    expect(screen.queryByRole("button", { name: "删除 indoor" })).toBeNull();
    expect(screen.getByLabelText(/^选择方式/)).toBeDisabled();
    const labels = screen.getByLabelText(/^标签/);
    fireEvent.change(labels, { target: { value: "night" } });
    fireEvent.keyDown(labels, { key: "Enter" });
    expect(screen.getByRole("button", { name: "删除 night" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "保存属性" }));

    expect(await screen.findByText("已保存。")).toBeVisible();
    const request = fetchMock.mock.calls[2][0] as Request;
    expect(request.method).toBe("PUT");
    await expect(request.clone().json()).resolves.toEqual({
      name: "Street",
      settings: { labels: ["indoor", "outdoor", "night"] },
    });
  });

  it("deletes a project only after its name is typed", async () => {
    fetchMock
      .mockResolvedValueOnce(response(streetProject))
      .mockResolvedValueOnce(response(streetSettings()))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    renderAt("/projects/street/settings");

    const remove = await screen.findByRole("button", { name: "删除项目" });
    expect(remove).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/以确认/), { target: { value: "Street" } });
    fireEvent.click(remove);

    expect(await screen.findByRole("heading", { name: "创建第一个标注项目" })).toBeVisible();
    const request = fetchMock.mock.calls[2][0] as Request;
    expect(request.method).toBe("DELETE");
    expect(new URL(request.url).pathname).toBe("/api/projects/street");
  });
});
