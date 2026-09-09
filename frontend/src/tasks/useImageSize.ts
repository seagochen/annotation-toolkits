import { useEffect, useState } from "react";

/**
 * ImageCanvas needs to know an image's pixel size before it can render it
 * (see components/image-canvas/README.md), so detection/segmentation/depth
 * pages preload it once per item, the same way the browser would compute
 * `naturalWidth`/`naturalHeight` for a plain `<img>`.
 */
export function useImageSize(src: string | undefined) {
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  useEffect(() => {
    setSize(null);
    if (!src) return;
    let active = true;
    const image = new Image();
    image.onload = () => {
      if (active) setSize({ width: image.naturalWidth, height: image.naturalHeight });
    };
    image.src = src;
    return () => {
      active = false;
    };
  }, [src]);
  return size;
}
