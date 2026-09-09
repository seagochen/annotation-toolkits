import { describe, expect, it } from "vitest";

import {
  cloneRasterBuffer,
  createRasterBuffer,
  fillPolygon,
  stampAt,
  strokeSegment,
  toBase64,
  toImageData,
} from "./raster-buffer";

describe("raster-buffer", () => {
  it("creates a buffer filled with the requested value", () => {
    const buffer = createRasterBuffer(3, 2, 7);
    expect(buffer.width).toBe(3);
    expect(buffer.height).toBe(2);
    expect(Array.from(buffer.data)).toEqual([7, 7, 7, 7, 7, 7]);
  });

  it("clones independently of the source buffer", () => {
    const buffer = createRasterBuffer(2, 2);
    const clone = cloneRasterBuffer(buffer);
    clone.data[0] = 200;
    expect(buffer.data[0]).toBe(0);
  });

  it("stamps a circular region and clamps at the buffer edge", () => {
    const buffer = createRasterBuffer(5, 5);
    stampAt(buffer, { x: 2, y: 2 }, 1.5, () => 9);
    const grid = Array.from({ length: 5 }, (_, y) => Array.from(buffer.data.subarray(y * 5, y * 5 + 5)));
    expect(grid[2][2]).toBe(9); // center
    expect(grid[0][0]).toBe(0); // corner untouched
    stampAt(buffer, { x: 0, y: 0 }, 10, () => 5); // radius far exceeds buffer, must not throw or overflow
    expect(buffer.data.length).toBe(25);
  });

  it("applies a function of the previous value rather than overwriting blindly", () => {
    const buffer = createRasterBuffer(3, 3, 100);
    stampAt(buffer, { x: 1, y: 1 }, 5, (value) => Math.min(255, value + 50));
    expect(buffer.data[4]).toBe(150);
  });

  it("strokes a segment so coverage is continuous, not just at the endpoints", () => {
    const buffer = createRasterBuffer(20, 3);
    strokeSegment(buffer, { x: 0, y: 1.5 }, { x: 19, y: 1.5 }, 0.75, () => 255);
    const middleRow = Array.from(buffer.data.subarray(20, 40));
    expect(middleRow.every((value) => value === 255)).toBe(true);
  });

  it("fills a polygon using the even-odd rule", () => {
    const buffer = createRasterBuffer(6, 6);
    fillPolygon(
      buffer,
      [
        { x: 1, y: 1 },
        { x: 5, y: 1 },
        { x: 5, y: 5 },
        { x: 1, y: 5 },
      ],
      3,
    );
    expect(buffer.data[0]).toBe(0); // outside the square
    expect(buffer.data[2 * 6 + 2]).toBe(3); // inside the square
  });

  it("ignores degenerate polygons with fewer than three points", () => {
    const buffer = createRasterBuffer(4, 4);
    fillPolygon(buffer, [{ x: 0, y: 0 }, { x: 1, y: 1 }], 9);
    expect(Array.from(buffer.data).every((value) => value === 0)).toBe(true);
  });

  it("base64-encodes raw bytes exactly, including a chunk boundary", () => {
    const buffer = createRasterBuffer(0x8001, 1);
    buffer.data[0] = 1;
    buffer.data[0x7fff] = 200;
    buffer.data[0x8000] = 255;
    const encoded = toBase64(buffer);
    const decoded = Uint8Array.from(atob(encoded), (char) => char.charCodeAt(0));
    expect(decoded[0]).toBe(1);
    expect(decoded[0x7fff]).toBe(200);
    expect(decoded[0x8000]).toBe(255);
    expect(decoded.length).toBe(buffer.data.length);
  });

  it("converts to ImageData with a grayscale default and a custom colorize", () => {
    const buffer = createRasterBuffer(2, 1);
    buffer.data[0] = 10;
    buffer.data[1] = 20;
    const gray = toImageData(buffer);
    expect(Array.from(gray.data.subarray(0, 4))).toEqual([10, 10, 10, 255]);

    const tinted = toImageData(buffer, (value) => [value, 0, 0, 128]);
    expect(Array.from(tinted.data.subarray(4, 8))).toEqual([20, 0, 0, 128]);
  });
});
