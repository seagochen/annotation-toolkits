import { describe, expect, it } from "vitest";

import {
  MIN_POLYGON_POINTS,
  beginOrExtendDraft,
  beginVertexDrag,
  cancelDraft,
  clampPoint,
  clearPolygons,
  closeDraft,
  createPolygonToolState,
  createPolygons,
  deleteVertex,
  dragVertexTo,
  endVertexDrag,
  hitTestEdge,
  hitTestPolygon,
  hitTestVertex,
  insertVertex,
  pointInPolygon,
  pointerDownEdit,
  relabelPolygon,
  removePolygon,
  selectPolygon,
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

describe("polygon-tool vertex editing", () => {
  const square = [
    { x: 10, y: 10 },
    { x: 50, y: 10 },
    { x: 50, y: 50 },
    { x: 10, y: 50 },
  ];
  const bounds = { width: 100, height: 80 };

  function editable() {
    const [first, second] = createPolygons([
      { category: "material", points: square },
      { category: "crack", points: [{ x: 40, y: 40 }, { x: 90, y: 40 }, { x: 90, y: 70 }] },
    ]);
    return createPolygonToolState([first, second], "material");
  }

  it("creates polygons with distinct ids from stored values", () => {
    const state = editable();
    expect(state.polygons[0].id).not.toBe(state.polygons[1].id);
    expect(state.polygons[0]).toMatchObject({ category: "material", points: square });
  });

  it("hit-tests polygons topmost first, vertices with the selected polygon first, and edges", () => {
    const state = editable();
    const [first, second] = state.polygons;
    expect(pointInPolygon(square, { x: 30, y: 30 })).toBe(true);
    expect(pointInPolygon(square, { x: 60, y: 30 })).toBe(false);
    // (48, 41) is inside both; the later polygon is on top.
    expect(hitTestPolygon(state.polygons, { x: 48, y: 41 })).toBe(second.id);
    expect(hitTestPolygon(state.polygons, { x: 5, y: 5 })).toBeNull();

    expect(hitTestVertex(state, { x: 51, y: 49 }, 3)).toEqual({ polygonId: first.id, index: 2 });
    expect(hitTestVertex(state, { x: 70, y: 70 }, 3)).toBeNull();
    // Vertices of two polygons both in range: the selected polygon wins.
    const close = createPolygonToolState(
      createPolygons([
        { category: "a", points: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 0, y: 10 }] },
        { category: "b", points: [{ x: 1, y: 0 }, { x: 10, y: 5 }, { x: 5, y: 10 }] },
      ]),
    );
    expect(hitTestVertex(close, { x: 0.4, y: 0 }, 2)?.polygonId).toBe(close.polygons[1].id);
    const selectedFirst = selectPolygon(close, close.polygons[0].id);
    expect(hitTestVertex(selectedFirst, { x: 0.4, y: 0 }, 2)).toEqual({ polygonId: close.polygons[0].id, index: 0 });

    expect(hitTestEdge(first, { x: 30, y: 11 }, 2)).toEqual({ index: 0, point: { x: 30, y: 10 } });
    expect(hitTestEdge(first, { x: 9, y: 30 }, 2)).toEqual({ index: 3, point: { x: 10, y: 30 } });
    expect(hitTestEdge(first, { x: 30, y: 30 }, 2)).toBeNull();
  });

  it("drags a vertex inside the image bounds", () => {
    let state = editable();
    const id = state.polygons[0].id;
    state = beginVertexDrag(state, { polygonId: id, index: 1 });
    expect(state).toMatchObject({ selectedId: id, selectedVertex: 1, drag: { polygonId: id, index: 1 } });
    state = dragVertexTo(state, { x: 60, y: 5 }, bounds);
    expect(state.polygons[0].points[1]).toEqual({ x: 60, y: 5 });
    state = dragVertexTo(state, { x: 140, y: -3 }, bounds);
    expect(state.polygons[0].points[1]).toEqual({ x: 100, y: 0 });
    state = endVertexDrag(state);
    expect(state.drag).toBeNull();
    // Without a drag, moving does nothing.
    expect(dragVertexTo(state, { x: 1, y: 1 }, bounds)).toBe(state);
    expect(clampPoint({ x: -1, y: 90 }, bounds)).toEqual({ x: 0, y: 80 });
  });

  it("inserts and deletes vertices, never going below three points", () => {
    let state = editable();
    const id = state.polygons[0].id;
    state = insertVertex(state, id, 0, { x: 30, y: 10 });
    expect(state.polygons[0].points).toEqual([square[0], { x: 30, y: 10 }, ...square.slice(1)]);
    expect(state.selectedVertex).toBe(1);
    expect(insertVertex(state, id, 9, { x: 0, y: 0 })).toBe(state);

    state = deleteVertex(state, id, 1);
    expect(state.polygons[0].points).toEqual(square);
    state = deleteVertex(state, id, 0);
    expect(state.polygons[0].points).toEqual(square.slice(1));
    expect(deleteVertex(state, id, 0)).toBe(state);
    expect(state.polygons[0].points).toHaveLength(MIN_POLYGON_POINTS);
  });

  it("relabels and removes the selected polygon", () => {
    let state = editable();
    const [first, second] = state.polygons;
    state = selectPolygon(state, first.id);
    state = relabelPolygon(state, first.id, "crack");
    expect(state.polygons.map((polygon) => polygon.category)).toEqual(["crack", "crack"]);
    state = removePolygon(state, first.id);
    expect(state.polygons.map((polygon) => polygon.id)).toEqual([second.id]);
    expect(state.selectedId).toBeNull();
    state = clearPolygons(selectPolygon(state, second.id));
    expect(state).toMatchObject({ polygons: [], selectedId: null });
  });

  it("routes a pointer-down: vertex drag, edge insert, select, deselect", () => {
    let state = editable();
    const [first, second] = state.polygons;
    state = pointerDownEdit(state, { x: 30, y: 30 }, 3);
    expect(state).toMatchObject({ selectedId: first.id, selectedVertex: null, drag: null });

    state = pointerDownEdit(state, { x: 30, y: 11 }, 3);
    expect(state.polygons[0].points).toHaveLength(5);
    expect(state.drag).toEqual({ polygonId: first.id, index: 1 });
    state = endVertexDrag(dragVertexTo(state, { x: 30, y: 0 }, bounds));
    expect(state.polygons[0].points[1]).toEqual({ x: 30, y: 0 });

    state = pointerDownEdit(state, { x: 89, y: 69 }, 3);
    expect(state).toMatchObject({ selectedId: second.id, selectedVertex: 2, drag: { polygonId: second.id, index: 2 } });
    state = pointerDownEdit(endVertexDrag(state), { x: 2, y: 2 }, 3);
    expect(state).toMatchObject({ selectedId: null, selectedVertex: null });
  });
});
