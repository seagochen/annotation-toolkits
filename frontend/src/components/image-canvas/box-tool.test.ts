import { describe, expect, it } from "vitest";

import {
  beginCreate,
  beginMoveOrResize,
  cancelDrag,
  createBoxToolState,
  deleteBox,
  endDrag,
  hitTestBoxes,
  hitTestHandle,
  previewCreateRect,
  setCategory,
  updateDrag,
  type Box,
} from "./box-tool";

const bounds = { width: 200, height: 100 };

function box(overrides: Partial<Box> = {}): Box {
  return { id: "b1", category: "cat", x: 10, y: 10, width: 20, height: 20, ...overrides };
}

describe("box-tool", () => {
  it("creates a box by drag, normalizing a reversed rectangle", () => {
    let state = createBoxToolState();
    state = beginCreate(state, { x: 30, y: 30 }, "cat");
    expect(previewCreateRect(state, { x: 10, y: 5 })).toEqual({
      x: 10,
      y: 5,
      width: 20,
      height: 25,
    });
    state = endDrag(state, { x: 10, y: 5 }, bounds);
    expect(state.boxes).toHaveLength(1);
    expect(state.boxes[0]).toMatchObject({ category: "cat", x: 10, y: 5, width: 20, height: 25 });
    expect(state.selectedId).toBe(state.boxes[0].id);
    expect(state.drag).toBeNull();
  });

  it("drops a create-drag that ends up smaller than the minimum size", () => {
    let state = createBoxToolState();
    state = beginCreate(state, { x: 5, y: 5 }, "cat");
    state = endDrag(state, { x: 5.5, y: 5.5 }, bounds, 1);
    expect(state.boxes).toHaveLength(0);
  });

  it("selects and moves an existing box, clamped to image bounds", () => {
    let state = createBoxToolState([box()]);
    state = beginMoveOrResize(state, { x: 15, y: 15 }, 4);
    expect(state.selectedId).toBe("b1");
    state = updateDrag(state, { x: 300, y: 15 }, bounds);
    expect(state.boxes[0].x).toBe(bounds.width - state.boxes[0].width);
    state = endDrag(state, { x: 300, y: 15 }, bounds);
    expect(state.drag).toBeNull();
  });

  it("resizes from a corner handle and keeps the opposite corner fixed", () => {
    let state = createBoxToolState([box()]);
    state = beginMoveOrResize(state, { x: 15, y: 15 }, 4); // select via inside-hit first
    state = beginMoveOrResize(state, { x: 30, y: 30 }, 4); // now hit the se handle
    state = updateDrag(state, { x: 50, y: 40 }, bounds);
    expect(state.boxes[0]).toMatchObject({ x: 10, y: 10, width: 40, height: 30 });
  });

  it("hit-tests handles within tolerance and boxes by containment", () => {
    const target = box();
    expect(hitTestHandle(target, { x: 31, y: 31 }, 2)).toBe("se");
    expect(hitTestHandle(target, { x: 31, y: 31 }, 0.1)).toBeNull();
    expect(hitTestBoxes([target], { x: 15, y: 15 })).toBe("b1");
    expect(hitTestBoxes([target], { x: 0, y: 0 })).toBeNull();
  });

  it("deselects when a drag starts outside every box", () => {
    let state = createBoxToolState([box()]);
    state = { ...state, selectedId: "b1" };
    state = beginMoveOrResize(state, { x: 190, y: 90 }, 4);
    expect(state.selectedId).toBeNull();
    expect(state.drag).toBeNull();
  });

  it("cancels an in-progress drag without mutating boxes", () => {
    let state = createBoxToolState([box()]);
    state = beginMoveOrResize(state, { x: 15, y: 15 }, 4);
    state = updateDrag(state, { x: 50, y: 50 }, bounds);
    state = cancelDrag(state);
    expect(state.drag).toBeNull();
    // updateDrag already committed the move into box state; cancelDrag only
    // clears the in-progress drag marker, it does not roll geometry back.
    expect(state.boxes[0].x).toBe(45);
  });

  it("deletes a box and clears its selection", () => {
    let state = createBoxToolState([box()]);
    state = { ...state, selectedId: "b1" };
    state = deleteBox(state, "b1");
    expect(state.boxes).toHaveLength(0);
    expect(state.selectedId).toBeNull();
  });

  it("changes a box's category in place", () => {
    let state = createBoxToolState([box()]);
    state = setCategory(state, "b1", "dog");
    expect(state.boxes[0].category).toBe("dog");
  });
});
