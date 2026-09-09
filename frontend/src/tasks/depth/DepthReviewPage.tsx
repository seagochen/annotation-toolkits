import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "wouter";

import {
  getProject,
  getQueue,
  projectFileUrl,
  submitAnnotation,
  type ProjectDetail,
  type QueueResponse,
} from "../../api/client";
import { ErrorState, LoadingState } from "../../components/AsyncState";
import {
  ImageCanvas,
  type ImageCanvasLayer,
  type ImageCanvasPointerEvent,
  type Viewport,
} from "../../components/image-canvas";
import {
  createRasterBuffer,
  loadFromImageElement,
  stampAt,
  strokeSegment,
  toBase64,
  toImageData,
  type RasterBuffer,
} from "../../components/image-canvas/raster-buffer";
import { useImageSize } from "../useImageSize";

type QueueItem = QueueResponse["items"][number];
type ReadyState = { project: ProjectDetail; queue: QueueResponse };
type PageState =
  | { kind: "loading" }
  | ({ kind: "ready" } & ReadyState)
  | { kind: "error"; message: string };

const BLANK_DEPTH = 128;

function itemText(item: QueueItem, key: string): string {
  const value = item[key];
  return value == null ? "" : String(value);
}

function itemBaselinePath(item: QueueItem): string | null {
  const value = item.baseline_path;
  return typeof value === "string" ? value : null;
}

export function DepthReviewPage() {
  const { projectId = "" } = useParams();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [direction, setDirection] = useState<"raise" | "lower">("raise");
  const [strength, setStrength] = useState(16);
  const [radius, setRadius] = useState(24);
  const [raster, setRaster] = useState<RasterBuffer | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const viewportRef = useRef<Viewport>({ scale: 1, offset: { x: 0, y: 0 } });
  const painting = useRef(false);
  const lastPoint = useRef<{ x: number; y: number } | null>(null);

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    setSubmitError("");
    try {
      const [project, queue] = await Promise.all([
        getProject(projectId),
        getQueue(projectId),
      ]);
      if (project.task_type !== "depth") {
        setState({ kind: "error", message: "该项目不是深度图标注任务。" });
        return;
      }
      setRaster(null);
      setState({ kind: "ready", project, queue });
    } catch (error) {
      setState({
        kind: "error",
        message: error instanceof Error ? error.message : "未知错误",
      });
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const item = state.kind === "ready" ? state.queue.items[0] : undefined;
  const imagePath = item ? itemText(item, "image_path") : undefined;
  const imageUrl = imagePath ? projectFileUrl(projectId, imagePath) : undefined;
  const baselinePath = item ? itemBaselinePath(item) : null;
  const imageSize = useImageSize(imageUrl);

  useEffect(() => {
    if (!imageSize || raster) return;
    if (!baselinePath) {
      setRaster(createRasterBuffer(imageSize.width, imageSize.height, BLANK_DEPTH));
      return;
    }
    let active = true;
    const baseline = new Image();
    // The API is typically served from a different origin/port than the
    // frontend (see README quick start); without this the canvas read in
    // loadFromImageElement throws SecurityError on a "tainted" canvas.
    baseline.crossOrigin = "anonymous";
    baseline.onload = () => {
      if (active) setRaster(loadFromImageElement(baseline));
    };
    baseline.src = projectFileUrl(projectId, baselinePath);
    return () => {
      active = false;
    };
  }, [baselinePath, imageSize, projectId, raster]);

  const sign = direction === "raise" ? 1 : -1;

  const layer: ImageCanvasLayer = useMemo(
    () => ({
      id: "depth-raster",
      blendMode: "multiply",
      render(context) {
        if (!raster) return;
        const offscreen = document.createElement("canvas");
        offscreen.width = raster.width;
        offscreen.height = raster.height;
        const offscreenContext = offscreen.getContext("2d");
        if (!offscreenContext) return;
        offscreenContext.putImageData(toImageData(raster), 0, 0);
        context.drawImage(offscreen, 0, 0);
      },
    }),
    [raster],
  );

  function handlePointer(event: ImageCanvasPointerEvent) {
    if (!raster) return;
    const apply = (value: number) => value + sign * strength;
    if (event.phase === "down") {
      painting.current = true;
      lastPoint.current = event.image;
      setRaster((buffer) => {
        if (!buffer) return buffer;
        stampAt(buffer, event.image, radius, apply);
        return { ...buffer };
      });
    } else if (event.phase === "move" && painting.current && lastPoint.current) {
      const from = lastPoint.current;
      lastPoint.current = event.image;
      setRaster((buffer) => {
        if (!buffer) return buffer;
        strokeSegment(buffer, from, event.image, radius, apply);
        return { ...buffer };
      });
    } else if (event.phase === "up" || event.phase === "cancel") {
      painting.current = false;
      lastPoint.current = null;
    }
  }

  async function submit() {
    if (!item || !raster || submitting) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      await submitAnnotation(projectId, itemText(item, "item_id"), {
        image_size: { width: raster.width, height: raster.height },
        pixels: toBase64(raster),
      });
      const queue = await getQueue(projectId);
      setState((current) =>
        current.kind === "ready" ? { ...current, queue } : current,
      );
      setRaster(null);
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "未知错误");
    } finally {
      setSubmitting(false);
    }
  }

  if (state.kind === "loading") return <LoadingState>正在读取深度队列…</LoadingState>;
  if (state.kind === "error") {
    return <ErrorState message={state.message} onRetry={() => void load()} />;
  }
  if (!item) {
    return (
      <section className="state-panel review-complete">
        <p className="eyebrow">Queue complete</p>
        <h1>深度图标注已完成</h1>
        <p>当前没有待标注图像，所有结果均已原子写入本地灰度图。</p>
        <Link className="text-link" to={`/projects/${projectId}`}>返回项目详情</Link>
      </section>
    );
  }

  return (
    <section className="classification-workspace">
      <div className="review-topbar">
        <div>
          <Link className="back-link" to={`/projects/${projectId}`}>← {state.project.name}</Link>
          <p className="eyebrow">Depth-map brush</p>
          <h1>用画笔修正这张图像的深度</h1>
        </div>
        <div className="queue-count">
          <strong>{state.queue.total}</strong>
          <span>张待标注</span>
        </div>
      </div>

      <div className="classification-grid">
        <div className="depth-canvas">
          {imageSize && imageUrl && raster ? (
            <ImageCanvas
              alt={imagePath ?? ""}
              imageSize={imageSize}
              interactionMode="tool"
              layers={[layer]}
              onPointerEvent={handlePointer}
              onViewportChange={(next) => {
                viewportRef.current = next;
              }}
              src={imageUrl}
            />
          ) : (
            <LoadingState>正在读取基线深度图…</LoadingState>
          )}
        </div>
        <div className="label-selector">
          <h2>画笔设置</h2>
          {!baselinePath && (
            <p className="submit-hint">未找到基线深度图，已从中灰度（128）开始编辑。</p>
          )}
          <div role="group" aria-label="调整方向">
            <button aria-pressed={direction === "raise"} onClick={() => setDirection("raise")} type="button">
              提高深度
            </button>
            <button aria-pressed={direction === "lower"} onClick={() => setDirection("lower")} type="button">
              降低深度
            </button>
          </div>
          <label>
            画笔半径
            <input
              max={150}
              min={2}
              onChange={(event) => setRadius(Number(event.target.value))}
              type="range"
              value={radius}
            />
          </label>
          <label>
            调整强度
            <input
              max={64}
              min={1}
              onChange={(event) => setStrength(Number(event.target.value))}
              type="range"
              value={strength}
            />
          </label>
          <button
            className="classification-submit"
            disabled={submitting || !raster}
            onClick={() => void submit()}
            type="button"
          >
            {submitting ? "正在保存…" : "保存并继续"}
          </button>
          {submitError && (
            <div className="submit-error" role="alert">
              <span>保存失败：{submitError}</span>
              <button onClick={() => void submit()} type="button">重试保存</button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
