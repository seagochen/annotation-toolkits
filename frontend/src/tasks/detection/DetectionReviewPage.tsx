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
import { useImageSize } from "../useImageSize";
import {
  ImageCanvas,
  type ImageCanvasLayer,
  type ImageCanvasPointerEvent,
  type Point,
  type Viewport,
} from "../../components/image-canvas";
import {
  beginCreate,
  beginMoveOrResize,
  createBoxToolState,
  deleteBox,
  endDrag,
  previewCreateRect,
  setCategory,
  updateDrag,
  type Box,
  type BoxToolState,
} from "../../components/image-canvas/box-tool";

type QueueItem = QueueResponse["items"][number];
type ReadyState = { project: ProjectDetail; queue: QueueResponse };
type PageState =
  | { kind: "loading" }
  | ({ kind: "ready" } & ReadyState)
  | { kind: "error"; message: string };

const HANDLE_SCREEN_SIZE = 8;
const MIN_BOX_SCREEN_SIZE = 3;

function itemText(item: QueueItem, key: string): string {
  const value = item[key];
  return value == null ? "" : String(value);
}

function configuredCategories(project: ProjectDetail): string[] {
  const value = project.summary.categories;
  return Array.isArray(value)
    ? value.filter((category): category is string => typeof category === "string")
    : [];
}

export function DetectionReviewPage() {
  const { projectId = "" } = useParams();
  const [state, setState] = useState<PageState>({ kind: "loading" });
  const [tool, setTool] = useState<BoxToolState>(createBoxToolState());
  const [mode, setMode] = useState<"select" | "draw">("select");
  const [category, setCurrentCategory] = useState("");
  const [previewPoint, setPreviewPoint] = useState<Point | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const viewportRef = useRef<Viewport>({ scale: 1, offset: { x: 0, y: 0 } });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    setSubmitError("");
    try {
      const [project, queue] = await Promise.all([
        getProject(projectId),
        getQueue(projectId),
      ]);
      if (project.task_type !== "detection") {
        setState({ kind: "error", message: "该项目不是目标检测任务。" });
        return;
      }
      setTool(createBoxToolState());
      setMode("select");
      const categories = configuredCategories(project);
      setCurrentCategory(categories[0] ?? "");
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
  const imageSize = useImageSize(imageUrl);
  const categories = state.kind === "ready" ? configuredCategories(state.project) : [];

  const layer: ImageCanvasLayer = useMemo(
    () => ({
      id: "boxes",
      render(context, frame) {
        const scale = frame.viewport.scale;
        for (const box of tool.boxes) {
          const selected = box.id === tool.selectedId;
          context.lineWidth = (selected ? 3 : 2) / scale;
          context.strokeStyle = selected ? "#ff8a3d" : "#39c37a";
          context.strokeRect(box.x, box.y, box.width, box.height);
          context.font = `${12 / scale}px sans-serif`;
          context.fillStyle = selected ? "#ff8a3d" : "#39c37a";
          context.fillText(box.category, box.x + 2 / scale, box.y - 4 / scale);
          if (selected) {
            const handleSize = HANDLE_SCREEN_SIZE / scale;
            for (const [hx, hy] of [
              [box.x, box.y],
              [box.x + box.width / 2, box.y],
              [box.x + box.width, box.y],
              [box.x + box.width, box.y + box.height / 2],
              [box.x + box.width, box.y + box.height],
              [box.x + box.width / 2, box.y + box.height],
              [box.x, box.y + box.height],
              [box.x, box.y + box.height / 2],
            ]) {
              context.fillRect(hx - handleSize / 2, hy - handleSize / 2, handleSize, handleSize);
            }
          }
        }
        if (mode === "draw" && previewPoint) {
          const preview = previewCreateRect(tool, previewPoint);
          if (preview) {
            context.setLineDash([6 / scale, 4 / scale]);
            context.lineWidth = 2 / scale;
            context.strokeStyle = "#ff8a3d";
            context.strokeRect(preview.x, preview.y, preview.width, preview.height);
            context.setLineDash([]);
          }
        }
      },
    }),
    [mode, previewPoint, tool],
  );

  function handlePointer(event: ImageCanvasPointerEvent) {
    if (!imageSize) return;
    const bounds = imageSize;
    const handleSize = HANDLE_SCREEN_SIZE / viewportRef.current.scale;
    if (mode === "draw") {
      if (event.phase === "down") {
        setTool((current) => beginCreate(current, event.image, category));
        setPreviewPoint(event.image);
      } else if (event.phase === "move") {
        setPreviewPoint(event.image);
      } else if (event.phase === "up") {
        setPreviewPoint(null);
        setTool((current) =>
          endDrag(current, event.image, bounds, MIN_BOX_SCREEN_SIZE / viewportRef.current.scale),
        );
        setMode("select");
      } else if (event.phase === "cancel") {
        setPreviewPoint(null);
        setTool((current) => ({ ...current, drag: null }));
      }
      return;
    }
    if (event.phase === "down") {
      setTool((current) => beginMoveOrResize(current, event.image, handleSize));
    } else if (event.phase === "move") {
      setTool((current) => updateDrag(current, event.image, bounds));
    } else if (event.phase === "up" || event.phase === "cancel") {
      setTool((current) => ({ ...current, drag: null }));
    }
  }

  async function submit() {
    if (!item || !imageSize || submitting) return;
    setSubmitting(true);
    setSubmitError("");
    try {
      await submitAnnotation(projectId, itemText(item, "item_id"), {
        image_size: imageSize,
        boxes: tool.boxes.map(({ id: _id, ...box }: Box) => box),
      });
      const queue = await getQueue(projectId);
      setState((current) =>
        current.kind === "ready" ? { ...current, queue } : current,
      );
      setTool(createBoxToolState());
    } catch (error) {
      setSubmitError(error instanceof Error ? error.message : "未知错误");
    } finally {
      setSubmitting(false);
    }
  }

  if (state.kind === "loading") return <LoadingState>正在读取检测队列…</LoadingState>;
  if (state.kind === "error") {
    return <ErrorState message={state.message} onRetry={() => void load()} />;
  }
  if (!item) {
    return (
      <section className="state-panel review-complete">
        <p className="eyebrow">Queue complete</p>
        <h1>目标检测已完成</h1>
        <p>当前没有待标注图像，所有结果均已原子写入本地 JSON。</p>
        <Link className="text-link" to={`/projects/${projectId}`}>返回项目详情</Link>
      </section>
    );
  }

  return (
    <section className="classification-workspace">
      <div className="review-topbar">
        <div>
          <Link className="back-link" to={`/projects/${projectId}`}>← {state.project.name}</Link>
          <p className="eyebrow">Object detection</p>
          <h1>为这张图像标注检测框</h1>
        </div>
        <div className="queue-count">
          <strong>{state.queue.total}</strong>
          <span>张待标注</span>
        </div>
      </div>

      <div className="classification-grid">
        <div className="detection-canvas">
          {imageSize && imageUrl ? (
            <ImageCanvas
              alt={imagePath ?? ""}
              imageSize={imageSize}
              interactionMode="tool"
              layers={[layer]}
              onPointerEvent={handlePointer}
              onViewportChange={(next) => {
                viewportRef.current = next;
              }}
              shortcuts={[
                {
                  key: "delete",
                  description: "删除选中的框",
                  onTrigger: () =>
                    setTool((current) =>
                      current.selectedId ? deleteBox(current, current.selectedId) : current,
                    ),
                },
                {
                  key: "backspace",
                  description: "删除选中的框",
                  onTrigger: () =>
                    setTool((current) =>
                      current.selectedId ? deleteBox(current, current.selectedId) : current,
                    ),
                },
              ]}
              src={imageUrl}
            />
          ) : (
            <LoadingState>正在读取图像尺寸…</LoadingState>
          )}
        </div>
        <div className="label-selector">
          <h2>检测类别</h2>
          <div role="group" aria-label="检测类别">
            {categories.map((option) => (
              <label className={option === category ? "selected" : ""} key={option}>
                <input
                  checked={option === category}
                  name="detection-category"
                  onChange={() => setCurrentCategory(option)}
                  type="radio"
                />
                <span>{option}</span>
              </label>
            ))}
          </div>
          <button
            aria-pressed={mode === "draw"}
            className="classification-submit"
            onClick={() => setMode(mode === "draw" ? "select" : "draw")}
            type="button"
          >
            {mode === "draw" ? "取消绘制" : "绘制新框"}
          </button>
          {tool.selectedId && (
            <button
              onClick={() => setTool((current) => deleteBox(current, current.selectedId!))}
              type="button"
            >
              删除选中的框
            </button>
          )}
          <p>已标注 {tool.boxes.length} 个框</p>
          <button
            className="classification-submit"
            disabled={submitting || !imageSize}
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
