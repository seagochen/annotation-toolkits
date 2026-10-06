import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { getProject, getQueue, submitAnnotation } from "../../api/client";
import { useTaskQueue } from "./useTaskQueue";
vi.mock("../../api/client", () => ({ getProject: vi.fn(), getQueue: vi.fn(), submitAnnotation: vi.fn() }));
beforeEach(() => vi.resetAllMocks());
it("keeps the most recently requested image when responses arrive out of order", async () => {
  vi.mocked(getProject).mockResolvedValue({ task_type: "detection" } as never);
  const page = (id: string) => ({ items: [{ item_id: id }], total: 3 }) as never;
  vi.mocked(getQueue).mockResolvedValueOnce(page("a"));
  const { result } = renderHook(() => useTaskQueue("p", { taskType: "detection", wrongType: "wrong" }));
  await waitFor(() => expect(result.current.item?.item_id).toBe("a"));
  let resolveB!: (value: never) => void;
  vi.mocked(getQueue).mockImplementationOnce(() => new Promise(resolve => { resolveB = resolve; })).mockResolvedValueOnce(page("c"));
  let first!: Promise<void>;
  act(() => { first = result.current.browse({ status: "pending", offset: 1 }); });
  await act(async () => { await result.current.browse({ status: "pending", offset: 2 }); });
  expect(result.current.item?.item_id).toBe("c");
  await act(async () => { resolveB(page("b")); await first; });
  expect(result.current.item?.item_id).toBe("c");
});

it("ignores an older navigation error after a newer image loads", async () => {
  vi.mocked(getProject).mockResolvedValue({ task_type: "detection" } as never);
  const page = (id: string) => ({ items: [{ item_id: id }], total: 3 }) as never;
  vi.mocked(getQueue).mockResolvedValueOnce(page("a"));
  const { result } = renderHook(() => useTaskQueue("p", { taskType: "detection", wrongType: "wrong" }));
  await waitFor(() => expect(result.current.item?.item_id).toBe("a"));
  let rejectB!: (reason: Error) => void;
  vi.mocked(getQueue).mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectB = reject; })).mockResolvedValueOnce(page("c"));
  let first!: Promise<void>;
  act(() => { first = result.current.browse({ status: "pending", offset: 1 }); });
  await act(async () => { await result.current.browse({ status: "pending", offset: 2 }); });
  await act(async () => { rejectB(new Error("old request failed")); await first; });
  expect(result.current.item?.item_id).toBe("c");
  expect(result.current.state.kind).toBe("ready");
});

it("blocks navigation while a save is pending and invalidates an earlier browse", async () => {
  vi.mocked(getProject).mockResolvedValue({ task_type: "detection" } as never);
  const page = (id: string) => ({ items: [{ item_id: id }], total: 3 }) as never;
  vi.mocked(getQueue).mockResolvedValueOnce(page("a"));
  const { result } = renderHook(() => useTaskQueue("p", { taskType: "detection", wrongType: "wrong" }));
  await waitFor(() => expect(result.current.item?.item_id).toBe("a"));
  let resolveBrowse!: (value: never) => void;
  let resolveSave!: (value: never) => void;
  vi.mocked(getQueue).mockImplementationOnce(() => new Promise(resolve => { resolveBrowse = resolve; })).mockResolvedValueOnce(page("next"));
  vi.mocked(submitAnnotation).mockImplementationOnce(() => new Promise(resolve => { resolveSave = resolve; }));
  let browse!: Promise<void>;
  let save!: Promise<boolean>;
  act(() => { browse = result.current.browse({ status: "pending", offset: 1 }); });
  act(() => { save = result.current.submit("a", {}); });
  await act(async () => {
    await result.current.browse({ status: "pending", offset: 2 });
    resolveBrowse(page("b"));
    await browse;
  });
  expect(result.current.item?.item_id).toBe("a");
  expect(getQueue).toHaveBeenCalledTimes(2);
  await act(async () => { resolveSave({ item: {}, status: { state: "reviewing", details: {} } } as never); await save; });
  expect(result.current.item?.item_id).toBe("next");
  expect(result.current.savedIds.has("a")).toBe(true);
});
