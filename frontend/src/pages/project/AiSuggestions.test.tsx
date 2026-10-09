import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { hostedRequest } from "../../api/client";
import { AiSuggestions } from "./AiSuggestions";

vi.mock("../../api/client", () => ({ hostedRequest: vi.fn() }));
const request = vi.mocked(hostedRequest);
const input = { id: "stable-id", itemId: "image-id", engine: "owlv2", confidence: 0.25 };
const job = { id: input.id, request: input, status: "ready", sourceRevision: 0,
  result: { artifact: { candidates: [] }, polygons: [] } };
const queue = { items: [{ item_id: "image-id", image_path: "sample.png" }] };
beforeEach(() => { request.mockReset(); });

it("shows the persisted recoverable job after a lost submission reply and reuses its ID", async () => {
  let saved: { id: string; request: typeof input; status: string; result: typeof job.result | null } | null = null;
  const submitted: unknown[] = [];
  request.mockImplementation(async (path, body) => {
    if (path.endsWith("queue?limit=200")) return queue;
    if (body !== undefined) {
      submitted.push(body);
      if (!saved) {
        saved = { ...job, request: body as typeof input, id: (body as typeof input).id, status: "submitting", result: null };
        throw new Error("Reply unavailable");
      }
      saved = { ...saved, status: "running" };
      return saved;
    }
    return { items: saved ? [saved] : [] };
  });
  render(<AiSuggestions projectId="a" onApplied={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: "生成建议" }));
  fireEvent.click(await screen.findByRole("button", { name: "恢复提交" }));
  await screen.findByRole("button", { name: "刷新结果" });
  expect(submitted).toHaveLength(2);
  expect(submitted[1]).toEqual(submitted[0]);
});

it("requires a successful explicit apply before refreshing the project", async () => {
  const onApplied = vi.fn();
  let status = "ready";
  request.mockImplementation(async (path, body) => {
    if (path.endsWith("queue?limit=200")) return queue;
    if (body !== undefined) { status = "applied"; return { status }; }
    return { items: [{ ...job, status }] };
  });
  render(<AiSuggestions projectId="a" onApplied={onApplied} />);
  expect(onApplied).not.toHaveBeenCalled();
  fireEvent.click(await screen.findByRole("button", { name: "应用建议并继续编辑" }));
  await waitFor(() => expect(onApplied).toHaveBeenCalledTimes(1));
  expect(request).toHaveBeenCalledWith("/api/projects/a/ai-jobs/stable-id/apply", {});
});

it("ignores an old project action after navigation", async () => {
  const onApplied = vi.fn();
  let resolveApply: ((value: unknown) => void) | undefined;
  request.mockImplementation(async (path, body) => {
    if (body !== undefined) return new Promise((resolve) => { resolveApply = resolve; });
    if (path.endsWith("queue?limit=200")) return queue;
    return { items: path.startsWith("/api/projects/a/") ? [job] : [] };
  });
  const view = render(<AiSuggestions projectId="a" onApplied={onApplied} />);
  fireEvent.click(await screen.findByRole("button", { name: "应用建议并继续编辑" }));
  view.rerender(<AiSuggestions projectId="b" onApplied={onApplied} />);
  await screen.findByRole("button", { name: "生成建议" });
  resolveApply?.({ status: "applied" });
  await waitFor(() => expect(screen.queryByRole("button", { name: "应用建议并继续编辑" })).toBeNull());
  expect(onApplied).not.toHaveBeenCalled();
});
