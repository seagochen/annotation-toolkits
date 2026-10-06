import type { Point } from "./geometry";

/**
 * Shared pixel buffer for #21 segmentation's brush/polygon mask and #22
 * depth's brush raster (see image-canvas/README.md). Unlike the rest of
 * this package, `stampAt`/`strokeSegment`/`fillPolygon` mutate `data` in
 * place: a paint stroke can call `stampAt` dozens of times per animation
 * frame, and copying a multi-megapixel array on every call would make
 * brushing visibly lag. Callers that need a React state update after a
 * mutation should shallow-clone the wrapper object, not `data` itself.
 */
export type RasterBuffer = {
  readonly width: number;
  readonly height: number;
  readonly data: Uint8ClampedArray;
};

export function createRasterBuffer(width: number, height: number, fill = 0): RasterBuffer {
  const data = new Uint8ClampedArray(width * height);
  if (fill) data.fill(fill);
  return { width, height, data };
}

export function cloneRasterBuffer(buffer: RasterBuffer): RasterBuffer {
  return { width: buffer.width, height: buffer.height, data: new Uint8ClampedArray(buffer.data) };
}

export function stampAt(
  buffer: RasterBuffer,
  center: Point,
  radius: number,
  apply: (value: number) => number,
): RasterBuffer {
  const { width, height, data } = buffer;
  const radiusSquared = radius * radius;
  const minX = Math.max(0, Math.floor(center.x - radius));
  const maxX = Math.min(width - 1, Math.ceil(center.x + radius));
  const minY = Math.max(0, Math.floor(center.y - radius));
  const maxY = Math.min(height - 1, Math.ceil(center.y + radius));
  for (let y = minY; y <= maxY; y += 1) {
    for (let x = minX; x <= maxX; x += 1) {
      const dx = x + 0.5 - center.x;
      const dy = y + 0.5 - center.y;
      if (dx * dx + dy * dy > radiusSquared) continue;
      const index = y * width + x;
      data[index] = apply(data[index]);
    }
  }
  return buffer;
}

export function strokeSegment(
  buffer: RasterBuffer,
  from: Point,
  to: Point,
  radius: number,
  apply: (value: number) => number,
): RasterBuffer {
  const distance = Math.hypot(to.x - from.x, to.y - from.y);
  const step = Math.max(radius / 2, 0.5);
  const steps = Math.max(1, Math.ceil(distance / step));
  for (let index = 0; index <= steps; index += 1) {
    const t = index / steps;
    stampAt(buffer, { x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t }, radius, apply);
  }
  return buffer;
}

/** Even-odd scanline fill of a closed polygon (image-pixel coordinates). */
export function fillPolygon(buffer: RasterBuffer, points: readonly Point[], value: number): RasterBuffer {
  if (points.length < 3) return buffer;
  const { width, height, data } = buffer;
  const minY = Math.max(0, Math.floor(Math.min(...points.map((point) => point.y))));
  const maxY = Math.min(height - 1, Math.ceil(Math.max(...points.map((point) => point.y))));
  for (let y = minY; y <= maxY; y += 1) {
    const scanY = y + 0.5;
    const crossings: number[] = [];
    for (let index = 0; index < points.length; index += 1) {
      const a = points[index];
      const b = points[(index + 1) % points.length];
      if ((a.y <= scanY && b.y > scanY) || (b.y <= scanY && a.y > scanY)) {
        const t = (scanY - a.y) / (b.y - a.y);
        crossings.push(a.x + t * (b.x - a.x));
      }
    }
    crossings.sort((a, b) => a - b);
    for (let index = 0; index + 1 < crossings.length; index += 2) {
      const startX = Math.max(0, Math.round(crossings[index]));
      const endX = Math.min(width - 1, Math.round(crossings[index + 1]) - 1);
      for (let x = startX; x <= endX; x += 1) data[y * width + x] = value;
    }
  }
  return buffer;
}

/** Encode raw bytes as base64, chunked to stay within call-stack argument limits. */
export function toBase64(buffer: RasterBuffer): string {
  const chunkSize = 0x8000;
  let binary = "";
  for (let offset = 0; offset < buffer.data.length; offset += chunkSize) {
    binary += String.fromCharCode(...buffer.data.subarray(offset, offset + chunkSize));
  }
  return btoa(binary);
}

/** Hydrate a buffer from a loaded image's red channel (grayscale PNGs decode to equal R/G/B). */
export function loadFromImageElement(image: HTMLImageElement): RasterBuffer {
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth;
  canvas.height = image.naturalHeight;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("2d canvas context is unavailable");
  context.drawImage(image, 0, 0);
  const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
  const data = new Uint8ClampedArray(canvas.width * canvas.height);
  for (let index = 0; index < data.length; index += 1) data[index] = pixels[index * 4];
  return { width: canvas.width, height: canvas.height, data };
}

export type Colorize = (value: number) => readonly [number, number, number, number];

const GRAYSCALE: Colorize = (value) => [value, value, value, 255];

export function toImageData(buffer: RasterBuffer, colorize: Colorize = GRAYSCALE): ImageData {
  const out = new ImageData(buffer.width, buffer.height);
  for (let index = 0; index < buffer.data.length; index += 1) {
    const [r, g, b, a] = colorize(buffer.data[index]);
    out.data[index * 4] = r;
    out.data[index * 4 + 1] = g;
    out.data[index * 4 + 2] = b;
    out.data[index * 4 + 3] = a;
  }
  return out;
}
