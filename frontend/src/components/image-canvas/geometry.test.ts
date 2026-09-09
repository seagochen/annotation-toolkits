import { describe, expect, it } from "vitest";

import {
  fitViewport,
  imageToScreen,
  panBy,
  screenToImage,
  zoomAt,
  type Viewport,
} from "./geometry";

describe("image canvas geometry", () => {
  it("fits and centers an image without changing its aspect ratio", () => {
    expect(
      fitViewport({ width: 1000, height: 500 }, { width: 800, height: 600 }, 0),
    ).toEqual({ scale: 0.8, offset: { x: 0, y: 100 } });
  });

  it("round-trips original pixels after zoom and pan", () => {
    const viewport = panBy(
      zoomAt(
        { scale: 0.75, offset: { x: 38.5, y: -12.25 } },
        3.2,
        { x: 411.25, y: 206.75 },
      ),
      { x: -93.125, y: 47.875 },
    );
    const image = { x: 713.123456, y: 287.654321 };
    const result = screenToImage(imageToScreen(image, viewport), viewport);
    expect(result.x).toBeCloseTo(image.x, 10);
    expect(result.y).toBeCloseTo(image.y, 10);
  });

  it("keeps the image point below a zoom anchor fixed and clamps scale", () => {
    const viewport: Viewport = { scale: 2, offset: { x: 20, y: 30 } };
    const anchor = { x: 400, y: 260 };
    const imageBefore = screenToImage(anchor, viewport);
    const zoomed = zoomAt(viewport, 100, anchor, { min: 0.5, max: 5 });
    expect(zoomed.scale).toBe(5);
    expect(imageToScreen(imageBefore, zoomed).x).toBeCloseTo(anchor.x, 12);
    expect(imageToScreen(imageBefore, zoomed).y).toBeCloseTo(anchor.y, 12);
  });

  it("rejects invalid dimensions and scales", () => {
    expect(() =>
      fitViewport({ width: 0, height: 10 }, { width: 100, height: 100 }),
    ).toThrow(/image width/);
    expect(() =>
      screenToImage({ x: 1, y: 1 }, { scale: 0, offset: { x: 0, y: 0 } }),
    ).toThrow(/viewport scale/);
  });
});
