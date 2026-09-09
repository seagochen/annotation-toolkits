import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// jsdom does not implement the Canvas 2D pixel-buffer types. Tests that only
// need the plain data container (not real rendering) can use this minimal
// stand-in instead of pulling in a canvas polyfill package.
if (typeof globalThis.ImageData === "undefined") {
  class ImageDataPolyfill {
    readonly data: Uint8ClampedArray;
    readonly width: number;
    readonly height: number;
    constructor(width: number, height: number) {
      this.width = width;
      this.height = height;
      this.data = new Uint8ClampedArray(width * height * 4);
    }
  }
  // @ts-expect-error -- test-only stand-in, not a spec-complete ImageData.
  globalThis.ImageData = ImageDataPolyfill;
}

afterEach(() => {
  cleanup();
});
