import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { ImageCanvasPointerEvent } from "./ImageCanvas";
import { useRasterBrush } from "./raster-brush";
import { createRasterBuffer, type RasterBuffer } from "./raster-buffer";

function pointer(phase: ImageCanvasPointerEvent["phase"], x: number, y: number) {
  return {
    phase,
    image: { x, y },
    screen: { x, y },
    pointerId: 1,
    pointerType: "mouse",
    buttons: phase === "up" ? 0 : 1,
    pressure: 0.5,
    shiftKey: false,
    altKey: false,
    ctrlKey: false,
    metaKey: false,
  } as ImageCanvasPointerEvent;
}

function setup() {
  return renderHook(() => {
    const [raster, setRaster] = useState<RasterBuffer | null>(createRasterBuffer(10, 1, 0));
    return { raster, paint: useRasterBrush(setRaster) };
  });
}

const painted = (raster: RasterBuffer | null) => Array.from(raster?.data ?? []);

describe("useRasterBrush", () => {
  it("stamps on down and strokes from the last point on move", () => {
    const { result } = setup();
    act(() => result.current.paint(pointer("down", 0.5, 0.5), 0.5, () => 9));
    expect(painted(result.current.raster)).toEqual([9, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
    act(() => result.current.paint(pointer("move", 4.5, 0.5), 0.5, () => 9));
    expect(painted(result.current.raster)).toEqual([9, 9, 9, 9, 9, 0, 0, 0, 0, 0]);
  });

  it("ignores moves after the stroke ended", () => {
    const { result } = setup();
    act(() => result.current.paint(pointer("down", 0.5, 0.5), 0.5, () => 1));
    act(() => result.current.paint(pointer("up", 0.5, 0.5), 0.5, () => 1));
    act(() => result.current.paint(pointer("move", 8.5, 0.5), 0.5, () => 1));
    expect(painted(result.current.raster)).toEqual([1, 0, 0, 0, 0, 0, 0, 0, 0, 0]);
  });

  it("yields a new buffer object so React re-renders the layer", () => {
    const { result } = setup();
    const before = result.current.raster;
    act(() => result.current.paint(pointer("down", 2.5, 0.5), 0.5, (value) => value + 3));
    expect(result.current.raster).not.toBe(before);
    expect(painted(result.current.raster)[2]).toBe(3);
  });
});
