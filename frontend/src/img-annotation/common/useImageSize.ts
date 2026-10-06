import { useEffect, useMemo, useState } from "react";

/**
 * ImageCanvas needs to know an image's pixel size before it can render it
 * (see img-annotation/common/image-canvas/README.md), so detection/segmentation/depth
 * pages preload it once per item, the same way the browser would compute
 * `naturalWidth`/`naturalHeight` for a plain `<img>`.
 */
export function useImageSize(src: string | undefined) {
  const [loaded, setLoaded] = useState<{ src: string; width: number; height: number } | null>(null);
  useEffect(() => {
    if (!src) return;
    let active = true;
    const image = new Image();
    image.onload = () => {
      if (active) setLoaded({ src, width: image.naturalWidth, height: image.naturalHeight });
    };
    image.src = src;
    return () => {
      active = false;
    };
  }, [src]);
  // Keyed by src: right after the item changes, the previous image's size
  // must not be used (a mask would be allocated at the wrong size).
  return useMemo(
    () => (loaded && loaded.src === src ? { width: loaded.width, height: loaded.height } : null),
    [loaded, src],
  );
}
