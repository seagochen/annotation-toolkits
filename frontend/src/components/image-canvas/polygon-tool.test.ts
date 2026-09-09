import { describe, expect, it } from "vitest";

import {
  beginOrExtendDraft,
  cancelDraft,
  clearPolygons,
  closeDraft,
  createPolygonToolState,
  removePolygon,
  setCategory,
  undoLastPoint,
} from "./polygon-tool";

describe("polygon-tool", () => {
  it("builds a draft point by point and closes it by clicking near the first vertex", () => {
    let state = createPolygonToolState([], "road");
    state = beginOrExtendDraft(state, { x: 0, y: 0 }, 6);
    state = beginOrExtendDraft(state, { x: 10, y: 0 }, 6);
    state = beginOrExtendDraft(state, { x: 10, y: 10 }, 6);
    expect(state.draft).toHaveLength(3);
    state = beginOrExtendDraft(state, { x: 2, y: 2 }, 6); // within closeDistance of (0,0)
    expect(state.draft).toBeNull();
    expect(state.polygons).toHaveLength(1);
    expect(state.polygons[0]).toMatchObject({ category: "road", points: [
      { x: 0, y: 0 },
      { x: 10, y: 0 },
      { x: 10, y: 10 },
    ] });
  });

  it("does not close a draft with fewer than three points even when near the start", () => {
    let state = createPolygonToolState();
    state = beginOrExtendDraft(state, { x: 0, y: 0 }, 6);
    state = beginOrExtendDraft(state, { x: 1, y: 1 }, 6); // close to start, but only 2 points
    expect(state.draft).toHaveLength(2);
  });

  it("explicitly closes a draft via closeDraft, discarding drafts under three points", () => {
    let state = createPolygonToolState();
    state = beginOrExtendDraft(state, { x: 0, y: 0 }, 6);
    state = beginOrExtendDraft(state, { x: 5, y: 5 }, 6);
    state = closeDraft(state); // only two points
    expect(state.draft).toBeNull();
    expect(state.polygons).toHaveLength(0);
  });

  it("undoes the last drafted point and clears the draft once empty", () => {
    let state = createPolygonToolState();
    state = beginOrExtendDraft(state, { x: 0, y: 0 }, 6);
    state = beginOrExtendDraft(state, { x: 5, y: 5 }, 6);
    state = undoLastPoint(state);
    expect(state.draft).toHaveLength(1);
    state = undoLastPoint(state);
    expect(state.draft).toBeNull();
  });

  it("cancels a draft without touching committed polygons", () => {
    let state = createPolygonToolState([{ id: "p1", category: "road", points: [] }]);
    state = beginOrExtendDraft(state, { x: 0, y: 0 }, 6);
    state = cancelDraft(state);
    expect(state.draft).toBeNull();
    expect(state.polygons).toHaveLength(1);
  });

  it("removes a polygon by id and clears all polygons", () => {
    let state = createPolygonToolState([
      { id: "p1", category: "road", points: [] },
      { id: "p2", category: "road", points: [] },
    ]);
    state = removePolygon(state, "p1");
    expect(state.polygons.map((polygon) => polygon.id)).toEqual(["p2"]);
    state = clearPolygons(state);
    expect(state.polygons).toHaveLength(0);
  });

  it("changes the active category used for new polygons", () => {
    let state = createPolygonToolState([], "road");
    state = setCategory(state, "building");
    expect(state.category).toBe("building");
  });
});
