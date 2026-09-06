import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ImageCanvas, type ImageCanvasLayer } from "./ImageCanvas";

const context = {
  setTransform: vi.fn(),
  clearRect: vi.fn(),
  save: vi.fn(),
  translate: vi.fn(),
  scale: vi.fn(),
  restore: vi.fn(),
} as unknown as CanvasRenderingContext2D;

function firePointer(
  target: Element,
  type: "pointerdown" | "pointermove" | "pointerup",
  init: Readonly<{
    clientX: number;
    clientY: number;
    pointerId: number;
    pointerType?: string;
    pressure?: number;
    buttons?: number;
  }>,
) {
  const event = new MouseEvent(type, {
    bubbles: true,
    buttons: init.buttons ?? 0,
    clientX: init.clientX,
    clientY: init.clientY,
  });
  Object.defineProperties(event, {
    pointerId: { value: init.pointerId },
    pointerType: { value: init.pointerType ?? "mouse" },
    pressure: { value: init.pressure ?? 0 },
  });
  fireEvent(target, event);
}

describe("ImageCanvas", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context);
  });

  it("renders the static image and overlay layers in declaration order", () => {
    const order: string[] = [];
    const layers: ImageCanvasLayer[] = [
      { id: "lower", render: () => order.push("lower") },
      { id: "upper", render: () => order.push("upper") },
    ];
    const { container } = render(
      <ImageCanvas
        alt="test canvas"
        imageSize={{ width: 1000, height: 500 }}
        layers={layers}
        src="/image.jpg"
        viewportSize={{ width: 800, height: 600 }}
      />,
    );
    expect(screen.getByRole("application", { name: "test canvas" })).toBeVisible();
    expect(container.querySelector("img")).toHaveAttribute("src", "/image.jpg");
    expect(container.querySelectorAll("canvas")).toHaveLength(2);
    expect(order.length).toBeGreaterThanOrEqual(2);
    for (let index = 0; index < order.length; index += 2) {
      expect(order.slice(index, index + 2)).toEqual(["lower", "upper"]);
    }
  });

  it("pans with pointer movement and zooms with keyboard", () => {
    const changed = vi.fn();
    render(
      <ImageCanvas
        alt="navigation canvas"
        defaultViewport={{ scale: 1, offset: { x: 0, y: 0 } }}
        imageSize={{ width: 400, height: 300 }}
        onViewportChange={changed}
        src="/image.jpg"
        viewportSize={{ width: 800, height: 600 }}
      />,
    );
    const canvas = screen.getByRole("application", { name: "navigation canvas" });
    firePointer(canvas, "pointerdown", { clientX: 10, clientY: 20, pointerId: 7 });
    firePointer(canvas, "pointermove", { clientX: 35, clientY: 50, pointerId: 7 });
    firePointer(canvas, "pointermove", { clientX: 40, clientY: 60, pointerId: 7 });
    expect(changed).toHaveBeenLastCalledWith({ scale: 1, offset: { x: 30, y: 40 } });
    fireEvent.keyDown(canvas, { key: "+" });
    const lastViewport = changed.mock.calls[changed.mock.calls.length - 1]?.[0];
    expect(lastViewport.scale).toBeCloseTo(1.2);
  });

  it("emits image-space tool coordinates and reports shortcut conflicts", () => {
    const pointer = vi.fn();
    const custom = vi.fn();
    const conflicts = vi.fn();
    render(
      <ImageCanvas
        alt="tool canvas"
        defaultViewport={{ scale: 2, offset: { x: 10, y: 20 } }}
        imageSize={{ width: 400, height: 300 }}
        interactionMode="tool"
        onPointerEvent={pointer}
        onShortcutConflict={conflicts}
        shortcuts={[
          { key: "+", description: "conflict", onTrigger: vi.fn() },
          { key: "x", description: "custom tool", onTrigger: custom },
        ]}
        src="/image.jpg"
        viewportSize={{ width: 800, height: 600 }}
      />,
    );
    const canvas = screen.getByRole("application", { name: "tool canvas" });
    firePointer(canvas, "pointerdown", {
      clientX: 210,
      clientY: 120,
      pointerId: 3,
      pointerType: "pen",
      pressure: 0.6,
      buttons: 1,
    });
    expect(pointer).toHaveBeenCalledWith(
      expect.objectContaining({
        phase: "down",
        image: { x: 100, y: 50 },
        pointerType: "pen",
      }),
    );
    expect(conflicts).toHaveBeenCalledWith(["+"]);
    fireEvent.keyDown(canvas, { key: "X" });
    expect(custom).toHaveBeenCalledOnce();
  });

  it("does not fire canvas shortcuts from its control buttons", () => {
    const custom = vi.fn();
    render(
      <ImageCanvas
        alt="editable guard"
        imageSize={{ width: 400, height: 300 }}
        shortcuts={[{ key: "x", description: "tool", onTrigger: custom }]}
        src="/image.jpg"
        viewportSize={{ width: 800, height: 600 }}
      />,
    );
    fireEvent.keyDown(screen.getByRole("button", { name: "适应窗口" }), { key: "x" });
    expect(custom).not.toHaveBeenCalled();
  });
});
