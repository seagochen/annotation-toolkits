import { useMemo, useRef, useState } from "react";
import { useParams } from "wouter";

import { projectFileUrl } from "../../api/client";
import { LoadingState } from "../../components/AsyncState";
import {
  AnnotationTabs,
  CANVAS_HINTS,
  OptionList,
  SubmitBar,
  TaskWorkspace,
} from "../../components/workspace/TaskWorkspace";
import { categoryColor } from "../../components/workspace/palette";
import { BoxIcon, HandIcon, SelectIcon } from "../../components/workspace/tool-icons";
import { useEditHistory, useRecordChanges } from "../../components/workspace/useEditHistory";
import {
  DIGIT_KEYS,
  MOD_LABEL,
  REDO_KEYS,
  SAVE_KEYS,
  UNDO_KEYS,
  useHotkeys,
  type Hotkey,
} from "../../components/workspace/useHotkeys";
import { summaryProgress } from "../../project-meta";
import { ImageStrip } from "../ImageStrip";
import { useImageSize } from "../useImageSize";
import { QueueFallback, itemText, summaryStrings, useResetOnItem, useTaskQueue } from "../useTaskQueue";
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

const HANDLE_SCREEN_SIZE = 8;
const MIN_BOX_SCREEN_SIZE = 3;

export function DetectionReviewPage() {
  const { projectId = "" } = useParams();
  const [tool, setTool] = useState<BoxToolState>(createBoxToolState());
  const [mode, setMode] = useState<"select" | "pan" | "draw">("select");
  const [category, setCurrentCategory] = useState("");
  const [previewPoint, setPreviewPoint] = useState<Point | null>(null);
  const viewportRef = useRef<Viewport>({ scale: 1, offset: { x: 0, y: 0 } });

  const queue = useTaskQueue(projectId, {
    taskType: "detection",
    wrongType: "该项目不是目标检测任务。",
    onLoad: (project) => {
      setCurrentCategory(summaryStrings(project, "categories")[0] ?? "");
    },
  });
  const { ready, item, submitting, submitError } = queue;
  const itemId = item ? itemText(item, "item_id") : "";
  useResetOnItem(itemId, () => {
    setTool(createBoxToolState());
    setPreviewPoint(null);
    if (mode === "draw") setMode("select");
  });
  const history = useEditHistory<readonly Box[]>();
  const markApplied = useRecordChanges(history, tool.drag ? null : tool.boxes, itemId);
  const imagePath = item ? itemText(item, "image_path") : undefined;
  const imageUrl = imagePath ? projectFileUrl(projectId, imagePath) : undefined;
  const imageSize = useImageSize(imageUrl);
  const categories = useMemo(
    () => (ready ? summaryStrings(ready.project, "categories") : []),
    [ready],
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

  const result = imageSize
    ? { image_size: imageSize, boxes: tool.boxes.map(({ id: _id, ...box }: Box) => box) }
    : undefined;

  async function submit() {
    if (!item || !result) return;
    await queue.submit(itemId, result);
  }

  function restore(step: (current: readonly Box[]) => readonly Box[] | undefined) {
    const boxes = step(tool.boxes);
    if (!boxes) return;
    markApplied(boxes);
    setPreviewPoint(null);
    setTool((current) => ({ ...current, boxes, selectedId: null, drag: null }));
  }
  const undo = () => restore(history.undo);
  const redo = () => restore(history.redo);

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

  function chooseMode(next: "select" | "pan" | "draw") {
    setPreviewPoint(null);
    setMode(next);
    if (next === "draw") setTool((current) => ({ ...current, selectedId: null }));
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
    { keys: ["v"], display: "V", description: "选择 / 编辑框", run: () => chooseMode("select") },
    { keys: ["h"], display: "H", description: "拖动画布", run: () => chooseMode("pan") },
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
    { keys: UNDO_KEYS, display: `${MOD_LABEL} + Z`, description: "撤销", run: undo },
    { keys: REDO_KEYS, display: `${MOD_LABEL} + Shift + Z`, description: "重做", run: redo },
    {
      keys: SAVE_KEYS,
      display: `${MOD_LABEL} + Enter`,
      description: "保存并继续",
      run: () => void submit(),
    },
  ];
  useHotkeys(hotkeys, Boolean(item));

  if (!ready || !item) {
    return (
      <QueueFallback
        doneDescription="当前没有待标注图像，所有结果均已原子写入本地 JSON。"
        doneTitle="目标检测已完成"
        loading="正在读取检测队列…"
        projectId={projectId}
        queue={queue}
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
      history={{ canUndo: history.canUndo, canRedo: history.canRedo, undo, redo }}
      panel={
        <>
          <AnnotationTabs
            classes={
              <>
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
              </>
            }
            count={tool.boxes.length}
            layers={
              tool.boxes.length === 0 ? (
                <p className="empty-note">还没有框。没有目标时可直接保存。</p>
              ) : (
                <ul className="item-list">
                  {tool.boxes.map((box, index) => (
                    <li key={box.id}>
                      <button
                        aria-pressed={box.id === tool.selectedId}
                        className="item-select"
                        onClick={() => {
                          setMode("select");
                          setTool((current) => ({ ...current, selectedId: box.id }));
                        }}
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
              )
            }
          />
          <p className="panel-note">
            {mode === "draw"
              ? `在图像上拖动，绘制一个「${category}」框。`
              : mode === "pan"
                ? "拖动平移画布；按 V 回到选择。"
                : "点击框选中，拖动移动，拖动控制点调整大小。"}
          </p>
        </>
      }
      progress={summaryProgress(ready.project.summary, ready.queue.total)}
      projectId={projectId}
      projectName={ready.project.name}
      rawData={result}
      remaining={ready.queue.total}
      stage={
        imageSize && imageUrl ? (
          <ImageCanvas
            alt={imagePath ?? ""}
            imageSize={imageSize}
            interactionMode={mode === "pan" ? "pan" : "tool"}
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
      strip={<ImageStrip dirty={history.canUndo} projectId={projectId} queue={queue} />}
      title="目标检测"
      tools={[
        [
          { id: "select", label: "选择", icon: <SelectIcon />, shortcut: "V", active: mode === "select", onSelect: () => chooseMode("select") },
          { id: "pan", label: "拖动画布", icon: <HandIcon />, shortcut: "H", active: mode === "pan", onSelect: () => chooseMode("pan") },
        ],
        [{ id: "box", label: "矩形框", icon: <BoxIcon />, shortcut: "B", active: mode === "draw", onSelect: () => chooseMode("draw") }],
      ]}
    />
  );
}
