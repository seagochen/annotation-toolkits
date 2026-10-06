export type Point = Readonly<{ x: number; y: number }>;
export type Size = Readonly<{ width: number; height: number }>;
export type Viewport = Readonly<{ scale: number; offset: Point }>;
export type ScaleLimits = Readonly<{ min: number; max: number }>;

export const DEFAULT_SCALE_LIMITS: ScaleLimits = { min: 0.05, max: 32 };

function positive(value: number, name: string): number {
  if (!Number.isFinite(value) || value <= 0) {
    throw new Error(`${name} must be a positive finite number`);
  }
  return value;
}

export function imageToScreen(point: Point, viewport: Viewport): Point {
  return {
    x: point.x * viewport.scale + viewport.offset.x,
    y: point.y * viewport.scale + viewport.offset.y,
  };
}

export function screenToImage(point: Point, viewport: Viewport): Point {
  positive(viewport.scale, "viewport scale");
  return {
    x: (point.x - viewport.offset.x) / viewport.scale,
    y: (point.y - viewport.offset.y) / viewport.scale,
  };
}

export function clampScale(scale: number, limits = DEFAULT_SCALE_LIMITS): number {
  positive(limits.min, "minimum scale");
  positive(limits.max, "maximum scale");
  if (limits.min > limits.max) throw new Error("minimum scale exceeds maximum scale");
  return Math.min(limits.max, Math.max(limits.min, scale));
}

export function zoomAt(
  viewport: Viewport,
  factor: number,
  anchor: Point,
  limits = DEFAULT_SCALE_LIMITS,
): Viewport {
  positive(factor, "zoom factor");
  const imageAnchor = screenToImage(anchor, viewport);
  const scale = clampScale(viewport.scale * factor, limits);
  return {
    scale,
    offset: {
      x: anchor.x - imageAnchor.x * scale,
      y: anchor.y - imageAnchor.y * scale,
    },
  };
}

export function panBy(viewport: Viewport, delta: Point): Viewport {
  return {
    ...viewport,
    offset: {
      x: viewport.offset.x + delta.x,
      y: viewport.offset.y + delta.y,
    },
  };
}

export function fitViewport(
  image: Size,
  container: Size,
  padding = 24,
  limits = DEFAULT_SCALE_LIMITS,
): Viewport {
  positive(image.width, "image width");
  positive(image.height, "image height");
  positive(container.width, "container width");
  positive(container.height, "container height");
  const availableWidth = Math.max(1, container.width - padding * 2);
  const availableHeight = Math.max(1, container.height - padding * 2);
  const scale = clampScale(
    Math.min(availableWidth / image.width, availableHeight / image.height),
    limits,
  );
  return {
    scale,
    offset: {
      x: (container.width - image.width * scale) / 2,
      y: (container.height - image.height * scale) / 2,
    },
  };
}
