import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "wouter";

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
  CANVAS_HINTS,
  CompleteState,
  OptionList,
  PanelSection,
  SubmitBar,
  TaskWorkspace,
} from "../../components/workspace/TaskWorkspace";
import { categoryColor } from "../../components/workspace/palette";
import { DIGIT_KEYS, MOD_LABEL, SAVE_KEYS, useHotkeys, type Hotkey } from "../../components/workspace/useHotkeys";
import { summaryProgress } from "../../project-meta";
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
  const categories = useMemo(
    () => (state.kind === "ready" ? configuredCategories(state.project) : []),
    [state],
  );

  const layer: ImageCanvasLayer = useMemo(
    () => ({
      id: "boxes",
      render(context, frame) {
        const scale = frame.viewport.scale;
        const fontSize = 12 / scale;
        context.font = `600 ${fontSize}px sans-serif`;
        for (const box of tool.boxes) {
          const selected = box.id === tool.selectedId;
          const color = categoryColor(categories.indexOf(box.category));
          context.lineWidth = (selected ? 3 : 2) / scale;
          context.strokeStyle = color;
          if (selected) {
            context.fillStyle = `${color}22`;
            context.fillRect(box.x, box.y, box.width, box.height);
          }
          context.strokeRect(box.x, box.y, box.width, box.height);
          // Label tag above the box (inside it when the box touches the top).
          const tagWidth = context.measureText(box.category).width + 8 / scale;
          const tagHeight = fontSize + 6 / scale;
          const tagY = box.y - tagHeight >= 0 ? box.y - tagHeight : box.y;
          context.fillStyle = color;
          context.fillRect(box.x, tagY, tagWidth, tagHeight);
          context.fillStyle = "#ffffff";
          context.fillText(box.category, box.x + 4 / scale, tagY + fontSize + 1 / scale);
          if (selected) {
            const handleSize = HANDLE_SCREEN_SIZE / scale;
            context.lineWidth = 1.5 / scale;
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
              context.fillStyle = "#ffffff";
              context.fillRect(hx - handleSize / 2, hy - handleSize / 2, handleSize, handleSize);
              context.strokeRect(hx - handleSize / 2, hy - handleSize / 2, handleSize, handleSize);
            }
          }
        }
        if (mode === "draw" && previewPoint) {
          const preview = previewCreateRect(tool, previewPoint);
          if (preview) {
            context.setLineDash([6 / scale, 4 / scale]);
            context.lineWidth = 2 / scale;
            context.strokeStyle = categoryColor(categories.indexOf(category));
            context.strokeRect(preview.x, preview.y, preview.width, preview.height);
            context.setLineDash([]);
          }
        }
      },
    }),
    [categories, category, mode, previewPoint, tool],
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

  function chooseCategory(next: string) {
    setCurrentCategory(next);
    // With a box selected, picking a category relabels that box.
    setTool((current) =>
      current.selectedId ? setCategory(current, current.selectedId, next) : current,
    );
  }

  function toggleDraw() {
    setMode((current) => (current === "draw" ? "select" : "draw"));
    setTool((current) => ({ ...current, selectedId: null }));
  }

  function deleteSelected() {
    setTool((current) => (current.selectedId ? deleteBox(current, current.selectedId) : current));
  }

  const hotkeys: Hotkey[] = [
    {
      keys: DIGIT_KEYS,
      display: "1–9",
      description: "选择类别（选中框时改为该类别）",
      run: (key) => {
        const next = categories[Number(key) - 1];
        if (next) chooseCategory(next);
      },
    },
    {
      keys: ["b"],
      display: "B",
      description: "绘制新框 / 取消绘制",
      run: toggleDraw,
    },
    {
      keys: ["delete", "backspace"],
      display: "Del",
      description: "删除选中的框",
      run: deleteSelected,
    },
    {
      keys: ["escape"],
      display: "Esc",
      description: "取消绘制 / 取消选中",
      run: () => {
        setPreviewPoint(null);
        setMode("select");
        setTool((current) => ({ ...current, selectedId: null, drag: null }));
      },
    },
    {
      keys: SAVE_KEYS,
      display: `${MOD_LABEL} + Enter`,
      description: "保存并继续",
      run: () => void submit(),
    },
  ];
  useHotkeys(hotkeys, Boolean(item));

  if (state.kind === "loading") return <LoadingState>正在读取检测队列…</LoadingState>;
  if (state.kind === "error") {
    return <ErrorState message={state.message} onRetry={() => void load()} />;
  }
  if (!item) {
    return (
      <CompleteState
        description="当前没有待标注图像，所有结果均已原子写入本地 JSON。"
        projectId={projectId}
        title="目标检测已完成"
      />
    );
  }

  const counts: Record<string, number> = {};
  for (const box of tool.boxes) counts[box.category] = (counts[box.category] ?? 0) + 1;
  const selectedBox = tool.boxes.find((box) => box.id === tool.selectedId);

  return (
    <TaskWorkspace
      fileName={imagePath}
      footer={
        <SubmitBar
          disabled={!imageSize}
          error={submitError}
          onSubmit={() => void submit()}
          submitting={submitting}
        />
      }
      hints={CANVAS_HINTS}
      hotkeys={hotkeys}
      panel={
        <>
          <PanelSection title="类别">
            <OptionList
              counts={counts}
              label="检测类别"
              name="detection-category"
              onToggle={chooseCategory}
              options={categories}
              selected={selectedBox ? [selectedBox.category] : [category]}
              swatches
            />
            {selectedBox && <p className="panel-note">已选中一个框：选择类别会修改它的类别。</p>}
          </PanelSection>
          <PanelSection title="工具">
            <button
              aria-pressed={mode === "draw"}
              className="secondary-action"
              onClick={toggleDraw}
              type="button"
            >
              {mode === "draw" ? "取消绘制" : "绘制新框"} <kbd aria-hidden="true">B</kbd>
            </button>
            <p className="panel-note">
              {mode === "draw"
                ? `在图像上拖动，绘制一个「${category}」框。`
                : "点击框选中，拖动移动，拖动控制点调整大小。"}
            </p>
          </PanelSection>
          <PanelSection title="标注框" aside={<span className="project-id">{tool.boxes.length} 个</span>}>
            {tool.boxes.length === 0 ? (
              <p className="empty-note">还没有框。没有目标时可直接保存。</p>
            ) : (
              <ul className="item-list">
                {tool.boxes.map((box, index) => (
                  <li key={box.id}>
                    <button
                      aria-pressed={box.id === tool.selectedId}
                      className="item-select"
                      onClick={() => setTool((current) => ({ ...current, selectedId: box.id }))}
                      type="button"
                    >
                      <span
                        aria-hidden="true"
                        className="option-swatch"
                        style={{ background: categoryColor(categories.indexOf(box.category)) }}
                      />
                      <span>
                        #{index + 1} {box.category}
                      </span>
                      <span className="item-meta">
                        {Math.round(box.width)}×{Math.round(box.height)}
                      </span>
                    </button>
                    <button
                      aria-label={`删除框 #${index + 1}`}
                      className="icon-button"
                      onClick={() => setTool((current) => deleteBox(current, box.id))}
                      type="button"
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </PanelSection>
        </>
      }
      progress={summaryProgress(state.project.summary, state.queue.total)}
      projectId={projectId}
      projectName={state.project.name}
      remaining={state.queue.total}
      stage={
        imageSize && imageUrl ? (
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
          <LoadingState>正在读取图像尺寸…</LoadingState>
        )
      }
      title="目标检测"
    />
  );
}
