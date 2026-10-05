import { useCallback, useRef, type Dispatch, type SetStateAction } from "react";

import type { Point } from "./geometry";
import type { ImageCanvasPointerEvent } from "./ImageCanvas";
import {
  stampAt,
  strokeSegment,
  toImageData,
  type Colorize,
  type RasterBuffer,
} from "./raster-buffer";

/**
 * Draw a raster onto a layer context in image coordinates.
 *
 * ImageData can only be put at 1:1 device pixels, so the raster goes through
 * an offscreen canvas and is drawn as an image, which the layer's viewport
 * transform then scales like everything else.
 */
export function drawRaster(
  context: CanvasRenderingContext2D,
  raster: RasterBuffer,
  colorize?: Colorize,
): void {
  const offscreen = document.createElement("canvas");
  offscreen.width = raster.width;
  offscreen.height = raster.height;
  const offscreenContext = offscreen.getContext("2d");
  if (!offscreenContext) return;
  offscreenContext.putImageData(toImageData(raster, colorize), 0, 0);
  context.drawImage(offscreen, 0, 0);
}

/**
 * The brush pointer state machine shared by raster editors: `down` stamps,
 * `move` while painting strokes from the last point, `up`/`cancel` ends the
 * stroke. Each change yields a new buffer object so React re-renders the
 * layer; the pixel array itself is edited in place.
 *
 * Returns a handler taking the pointer event plus the stroke's radius and
 * per-pixel `apply` (paint a class index, raise a depth value, ...).
 */
export function useRasterBrush(setRaster: Dispatch<SetStateAction<RasterBuffer | null>>) {
  const lastPoint = useRef<Point | null>(null);
  return useCallback(
    (event: ImageCanvasPointerEvent, radius: number, apply: (value: number) => number) => {
      if (event.phase === "down") {
        lastPoint.current = event.image;
        setRaster((buffer) => buffer && { ...stampAt(buffer, event.image, radius, apply) });
      } else if (event.phase === "move" && lastPoint.current) {
        const from = lastPoint.current;
        lastPoint.current = event.image;
        setRaster(
          (buffer) => buffer && { ...strokeSegment(buffer, from, event.image, radius, apply) },
        );
      } else if (event.phase === "up" || event.phase === "cancel") {
        lastPoint.current = null;
      }
    },
    [setRaster],
  );
}
