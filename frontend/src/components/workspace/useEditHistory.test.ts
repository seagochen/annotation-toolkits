import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useEditHistory, useRecordChanges } from "./useEditHistory";

describe("useEditHistory", () => {
  it("undoes and redoes recorded snapshots", () => {
    const { result } = renderHook(() => useEditHistory<number>());
    expect(result.current.canUndo).toBe(false);
    act(() => result.current.record(1));
    act(() => result.current.record(2));
    let restored: number | undefined;
    act(() => {
      restored = result.current.undo(3);
    });
    expect(restored).toBe(2);
    expect(result.current.canRedo).toBe(true);
    act(() => {
      restored = result.current.redo(2);
    });
    expect(restored).toBe(3);
    // A new edit after an undo drops the redo branch.
    act(() => {
      result.current.undo(3);
      result.current.record(2);
    });
    expect(result.current.canRedo).toBe(false);
  });

  it("keeps at most `limit` steps", () => {
    const { result } = renderHook(() => useEditHistory<number>(2));
    act(() => [1, 2, 3].forEach(result.current.record));
    let restored: (number | undefined)[] = [];
    act(() => {
      restored = [result.current.undo(4), result.current.undo(3), result.current.undo(2)];
    });
    expect(restored).toEqual([3, 2, undefined]);
  });
});

describe("useRecordChanges", () => {
  function setup() {
    return renderHook(
      ({ value, itemKey }: { value: readonly string[] | null; itemKey: string }) => {
        const history = useEditHistory<readonly string[]>();
        const markApplied = useRecordChanges(history, value, itemKey);
        return { history, markApplied };
      },
      { initialProps: { value: [] as readonly string[] | null, itemKey: "i1" } },
    );
  }

  it("records each settled value, treating an in-progress edit as one step", () => {
    const { result, rerender } = setup();
    const one = ["a"];
    rerender({ value: null, itemKey: "i1" });
    rerender({ value: one, itemKey: "i1" });
    rerender({ value: null, itemKey: "i1" });
    rerender({ value: ["a", "b"], itemKey: "i1" });
    let restored: readonly string[] | undefined;
    act(() => {
      restored = result.current.history.undo(["a", "b"]);
    });
    expect(restored).toBe(one);
  });

  it("does not record a value restored by undo, and starts over for a new item", () => {
    const { result, rerender } = setup();
    rerender({ value: ["a"], itemKey: "i1" });
    let restored: readonly string[] | undefined;
    act(() => {
      restored = result.current.history.undo(["a"]);
    });
    expect(restored).toEqual([]);
    act(() => result.current.markApplied(restored!));
    rerender({ value: restored!, itemKey: "i1" });
    expect(result.current.history.canUndo).toBe(false);
    expect(result.current.history.canRedo).toBe(true);

    rerender({ value: ["x"], itemKey: "i2" });
    expect(result.current.history.canUndo).toBe(false);
    expect(result.current.history.canRedo).toBe(false);
  });
});
