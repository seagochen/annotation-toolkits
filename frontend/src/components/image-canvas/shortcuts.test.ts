import { describe, expect, it, vi } from "vitest";

import { normalizeShortcutKey, resolveShortcuts } from "./shortcuts";

describe("image canvas shortcuts", () => {
  it("normalizes keys and resolves conflicts deterministically", () => {
    const first = vi.fn();
    const duplicate = vi.fn();
    const reserved = vi.fn();
    const result = resolveShortcuts(["+", "ArrowLeft"], [
      { key: " X ", description: "first", onTrigger: first },
      { key: "x", description: "duplicate", onTrigger: duplicate },
      { key: "+", description: "reserved", onTrigger: reserved },
    ]);
    expect(normalizeShortcutKey(" ArrowLeft ")).toBe("arrowleft");
    expect(result.conflicts).toEqual(["x", "+"]);
    result.bindings.get("x")?.onTrigger();
    expect(first).toHaveBeenCalledOnce();
    expect(duplicate).not.toHaveBeenCalled();
    expect(reserved).not.toHaveBeenCalled();
  });
});
