import { describe, expect, it } from "vitest";

import type { Box } from "./box-tool";
import type { Polygon } from "./polygon-tool";
import { createKeyPoints, eraseAt, hitTestKeyPoint, type Shapes } from "./shape-eraser";

const square: Polygon = {
  id: "p1",
  category: "a",
  points: [
    { x: 0, y: 0 },
    { x: 10, y: 0 },
    { x: 10, y: 10 },
    { x: 0, y: 10 },
  ],
};
const box: Box = { id: "b1", category: "a", x: 20, y: 20, width: 40, height: 40 };

function shapes(overrides: Partial<Shapes> = {}): Shapes {
  return {
    polygons: [square],
    boxes: [box],
    points: createKeyPoints([{ category: "a", x: 100, y: 100 }]),
    ...overrides,
  };
}

describe("eraseAt", () => {
  it("returns the same object when nothing is under the eraser", () => {
    const before = shapes();
    expect(eraseAt(before, { x: 200, y: 200 }, 5)).toBe(before);
  });

  it("removes keypoints inside the circle and keeps the other lists' identity", () => {
    const before = shapes();
    const after = eraseAt(before, { x: 103, y: 104 }, 5);
    expect(after.points).toEqual([]);
    expect(after.polygons).toBe(before.polygons);
    expect(after.boxes).toBe(before.boxes);
  });

  it("removes polygon vertices and re-forms the polygon from the rest", () => {
    const after = eraseAt(shapes(), { x: 10, y: 10 }, 1);
    expect(after.polygons).toHaveLength(1);
    expect(after.polygons[0].id).toBe("p1");
    expect(after.polygons[0].points).toEqual([
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 0, y: 10 },
    ]);
  });

  it("removes a polygon left with fewer than three vertices", () => {
    const after = eraseAt(shapes(), { x: 10, y: 5 }, 5);
    expect(after.polygons).toEqual([]);
  });

  it("removes a box when the circle touches its outline, not from deep inside it", () => {
    expect(eraseAt(shapes(), { x: 40, y: 40 }, 5).boxes).toHaveLength(1);
    expect(eraseAt(shapes(), { x: 40, y: 23 }, 5).boxes).toEqual([]); // inside, near the top edge
    expect(eraseAt(shapes(), { x: 64, y: 40 }, 5).boxes).toEqual([]); // outside, near the right edge
    expect(eraseAt(shapes(), { x: 66, y: 66 }, 5).boxes).toHaveLength(1); // corner just out of reach
  });
});

describe("hitTestKeyPoint", () => {
  it("finds the topmost point within the tolerance", () => {
    const points = createKeyPoints([
      { category: "a", x: 0, y: 0 },
      { category: "b", x: 1, y: 0 },
    ]);
    expect(hitTestKeyPoint(points, { x: 0.4, y: 0 }, 2)).toBe(points[1].id);
    expect(hitTestKeyPoint(points, { x: 5, y: 0 }, 2)).toBeNull();
  });
});
