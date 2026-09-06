import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
} from "react";

import {
  DEFAULT_SCALE_LIMITS,
  fitViewport,
  panBy,
  screenToImage,
  zoomAt,
  type Point,
  type ScaleLimits,
  type Size,
  type Viewport,
} from "./geometry";
import {
  normalizeShortcutKey,
  resolveShortcuts,
  type ShortcutBinding,
} from "./shortcuts";
import "./image-canvas.css";

export type ImageCanvasFrame = Readonly<{
  imageSize: Size;
  viewportSize: Size;
  viewport: Viewport;
}>;

export type ImageCanvasLayer = Readonly<{
  id: string;
  render: (context: CanvasRenderingContext2D, frame: ImageCanvasFrame) => void;
  visible?: boolean;
  opacity?: number;
  blendMode?: CSSProperties["mixBlendMode"];
}>;

export type ImageCanvasPointerEvent = Readonly<{
  phase: "down" | "move" | "up" | "cancel";
  image: Point;
  screen: Point;
  pointerId: number;
  pointerType: string;
  buttons: number;
  pressure: number;
  shiftKey: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
}>;

export type ImageCanvasProps = Readonly<{
  src: string;
  alt: string;
  imageSize: Size;
  layers?: readonly ImageCanvasLayer[];
  interactionMode?: "pan" | "tool";
  viewport?: Viewport;
  defaultViewport?: Viewport;
  viewportSize?: Size;
  scaleLimits?: ScaleLimits;
  shortcuts?: readonly ShortcutBinding[];
  onPointerEvent?: (event: ImageCanvasPointerEvent) => void;
  onViewportChange?: (viewport: Viewport) => void;
  onShortcutConflict?: (keys: readonly string[]) => void;
  className?: string;
}>;

const BUILT_IN_KEYS = ["+", "=", "-", "0", "arrowleft", "arrowright", "arrowup", "arrowdown"];
const KEYBOARD_PAN = 32;

function editableTarget(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && Boolean(target.closest("input, textarea, select, button, [contenteditable=true]"));
}

export function ImageCanvas({
  src,
  alt,
  imageSize,
  layers = [],
  interactionMode = "pan",
  viewport: controlledViewport,
  defaultViewport,
  viewportSize: fixedViewportSize,
  scaleLimits = DEFAULT_SCALE_LIMITS,
  shortcuts = [],
  onPointerEvent,
  onViewportChange,
  onShortcutConflict,
  className = "",
}: ImageCanvasProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const [measuredSize, setMeasuredSize] = useState<Size>({ width: 1, height: 1 });
  const canvasSize = fixedViewportSize ?? measuredSize;
  const initial = defaultViewport ?? fitViewport(imageSize, canvasSize, 24, scaleLimits);
  const [internalViewport, setInternalViewport] = useState<Viewport>(initial);
  const viewport = controlledViewport ?? internalViewport;
  const viewportRef = useRef(viewport);
  viewportRef.current = viewport;
  const drag = useRef<{ pointerId: number; last: Point } | null>(null);
  const spaceHeld = useRef(false);

  const setViewport = useCallback(
    (next: Viewport) => {
      viewportRef.current = next;
      if (controlledViewport === undefined) setInternalViewport(next);
      onViewportChange?.(next);
    },
    [controlledViewport, onViewportChange],
  );

  const fit = useCallback(() => {
    setViewport(fitViewport(imageSize, canvasSize, 24, scaleLimits));
  }, [canvasSize, imageSize, scaleLimits, setViewport]);

  useEffect(() => {
    if (fixedViewportSize) return;
    const host = hostRef.current;
    if (!host) return;
    const measure = () => {
      if (host.clientWidth > 0 && host.clientHeight > 0) {
        setMeasuredSize({ width: host.clientWidth, height: host.clientHeight });
      }
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(host);
    return () => observer.disconnect();
  }, [fixedViewportSize]);

  useEffect(() => {
    if (controlledViewport === undefined && defaultViewport === undefined) fit();
  }, [canvasSize.height, canvasSize.width, controlledViewport, defaultViewport, imageSize.height, imageSize.width]);

  const shortcutResolution = useMemo(
    () => resolveShortcuts(BUILT_IN_KEYS, shortcuts),
    [shortcuts],
  );
  useEffect(() => {
    if (shortcutResolution.conflicts.length) {
      onShortcutConflict?.(shortcutResolution.conflicts);
    }
  }, [onShortcutConflict, shortcutResolution]);

  const screenPoint = useCallback((clientX: number, clientY: number): Point => {
    const rect = hostRef.current?.getBoundingClientRect();
    return { x: clientX - (rect?.left ?? 0), y: clientY - (rect?.top ?? 0) };
  }, []);

  function emitPointer(
    phase: ImageCanvasPointerEvent["phase"],
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    const screen = screenPoint(event.clientX, event.clientY);
    onPointerEvent?.({
      phase,
      screen,
      image: screenToImage(screen, viewportRef.current),
      pointerId: event.pointerId,
      pointerType: event.pointerType,
      buttons: event.buttons,
      pressure: event.pressure,
      shiftKey: event.shiftKey,
      altKey: event.altKey,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
    });
  }

  function pointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    const navigate = interactionMode === "pan" || spaceHeld.current;
    if (navigate) {
      drag.current = {
        pointerId: event.pointerId,
        last: screenPoint(event.clientX, event.clientY),
      };
      event.currentTarget.setPointerCapture?.(event.pointerId);
    } else {
      event.currentTarget.setPointerCapture?.(event.pointerId);
      emitPointer("down", event);
    }
  }

  function pointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const current = screenPoint(event.clientX, event.clientY);
    if (drag.current?.pointerId === event.pointerId) {
      setViewport(
        panBy(viewportRef.current, {
          x: current.x - drag.current.last.x,
          y: current.y - drag.current.last.y,
        }),
      );
      drag.current.last = current;
    } else if (interactionMode === "tool") {
      emitPointer("move", event);
    }
  }

  function pointerEnd(
    phase: "up" | "cancel",
    event: ReactPointerEvent<HTMLDivElement>,
  ) {
    if (drag.current?.pointerId === event.pointerId) {
      drag.current = null;
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    } else if (interactionMode === "tool") {
      emitPointer(phase, event);
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    }
  }

  function wheel(event: ReactWheelEvent<HTMLDivElement>) {
    event.preventDefault();
    const anchor = screenPoint(event.clientX, event.clientY);
    setViewport(zoomAt(viewportRef.current, event.deltaY < 0 ? 1.15 : 1 / 1.15, anchor, scaleLimits));
  }

  function keyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (editableTarget(event.target)) return;
    if (event.code === "Space") {
      spaceHeld.current = true;
      event.preventDefault();
      return;
    }
    const key = normalizeShortcutKey(event.key);
    const center = { x: canvasSize.width / 2, y: canvasSize.height / 2 };
    let next: Viewport | undefined;
    const currentViewport = viewportRef.current;
    if (key === "+" || key === "=") next = zoomAt(currentViewport, 1.2, center, scaleLimits);
    else if (key === "-") next = zoomAt(currentViewport, 1 / 1.2, center, scaleLimits);
    else if (key === "0") next = fitViewport(imageSize, canvasSize, 24, scaleLimits);
    else if (key === "arrowleft") next = panBy(currentViewport, { x: -KEYBOARD_PAN, y: 0 });
    else if (key === "arrowright") next = panBy(currentViewport, { x: KEYBOARD_PAN, y: 0 });
    else if (key === "arrowup") next = panBy(currentViewport, { x: 0, y: -KEYBOARD_PAN });
    else if (key === "arrowdown") next = panBy(currentViewport, { x: 0, y: KEYBOARD_PAN });
    else {
      const custom = shortcutResolution.bindings.get(key);
      if (!custom) return;
      custom.onTrigger();
      event.preventDefault();
      return;
    }
    setViewport(next);
    event.preventDefault();
  }

  const imageTransform = `translate(${viewport.offset.x}px, ${viewport.offset.y}px) scale(${viewport.scale})`;
  return (
    <div
      aria-label={alt}
      className={`image-canvas ${className}`.trim()}
      data-interaction-mode={interactionMode}
      onBlur={() => {
        spaceHeld.current = false;
        drag.current = null;
      }}
      onKeyDown={keyDown}
      onKeyUp={(event) => {
        if (event.code === "Space") spaceHeld.current = false;
      }}
      onPointerCancel={(event) => pointerEnd("cancel", event)}
      onPointerDown={pointerDown}
      onPointerMove={pointerMove}
      onPointerUp={(event) => pointerEnd("up", event)}
      onWheel={wheel}
      ref={hostRef}
      role="application"
      tabIndex={0}
    >
      <img
        alt=""
        className="image-canvas-base"
        draggable={false}
        height={imageSize.height}
        src={src}
        style={{ transform: imageTransform }}
        width={imageSize.width}
      />
      {layers.map((layer) => (
        <CanvasLayer
          frame={{ imageSize, viewportSize: canvasSize, viewport }}
          key={layer.id}
          layer={layer}
        />
      ))}
      <div className="image-canvas-controls" aria-label="画布视图控制">
        <button aria-label="放大" onClick={() => setViewport(zoomAt(viewport, 1.2, { x: canvasSize.width / 2, y: canvasSize.height / 2 }, scaleLimits))} type="button">+</button>
        <button aria-label="缩小" onClick={() => setViewport(zoomAt(viewport, 1 / 1.2, { x: canvasSize.width / 2, y: canvasSize.height / 2 }, scaleLimits))} type="button">−</button>
        <button aria-label="适应窗口" onClick={fit} type="button">适应</button>
      </div>
    </div>
  );
}

function CanvasLayer({ layer, frame }: { layer: ImageCanvasLayer; frame: ImageCanvasFrame }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const canvas = ref.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context || layer.visible === false) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(frame.viewportSize.width * ratio);
    canvas.height = Math.round(frame.viewportSize.height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, frame.viewportSize.width, frame.viewportSize.height);
    context.save();
    context.translate(frame.viewport.offset.x, frame.viewport.offset.y);
    context.scale(frame.viewport.scale, frame.viewport.scale);
    layer.render(context, frame);
    context.restore();
  }, [frame, layer]);
  if (layer.visible === false) return null;
  return (
    <canvas
      aria-hidden="true"
      className="image-canvas-layer"
      ref={ref}
      style={{ mixBlendMode: layer.blendMode, opacity: layer.opacity ?? 1 }}
    />
  );
}
