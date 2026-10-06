import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "wouter";

import { projectFileUrl } from "../../api/client";
import { LoadingState } from "../../components/AsyncState";
import {
  CANVAS_HINTS,
  PanelSection,
  RangeField,
  SubmitBar,
  TaskWorkspace,
} from "../common/workspace/TaskWorkspace";
import { HandIcon, LowerIcon, RaiseIcon } from "../common/workspace/tool-icons";
import { useEditHistory } from "../common/workspace/useEditHistory";
import {
  MOD_LABEL,
  REDO_KEYS,
  SAVE_KEYS,
  UNDO_KEYS,
  useHotkeys,
  type Hotkey,
} from "../common/workspace/useHotkeys";
import { summaryProgress } from "../../project-meta";
import {
  ImageCanvas,
  drawRaster,
  useRasterBrush,
  type ImageCanvasLayer,
  type ImageCanvasPointerEvent,
  type Viewport,
} from "../common/image-canvas";
import {
  cloneRasterBuffer,
  createRasterBuffer,
  loadFromImageElement,
  toBase64,
  type RasterBuffer,
} from "../common/image-canvas/raster-buffer";
import { ImageStrip } from "../common/ImageStrip";
import { useImageSize } from "../common/useImageSize";
import { QueueFallback, itemText, useResetOnItem, useTaskQueue, type QueueItem } from "../common/useTaskQueue";

const BLANK_DEPTH = 128;
const MIN_RADIUS = 2;
const MAX_RADIUS = 150;
// Each undo step keeps a whole copy of the depth map.
const HISTORY_LIMIT = 20;

function itemBaselinePath(item: QueueItem): string | null {
  const value = item.baseline_path;
  return typeof value === "string" ? value : null;
}

export function DepthReviewPage() {
  const { projectId = "" } = useParams();
  const [tool, setTool] = useState<"pan" | "raise" | "lower">("raise");
  const [strength, setStrength] = useState(16);
  const [radius, setRadius] = useState(24);
  const [raster, setRaster] = useState<RasterBuffer | null>(null);
  const viewportRef = useRef<Viewport>({ scale: 1, offset: { x: 0, y: 0 } });
  const paint = useRasterBrush(setRaster);

  const queue = useTaskQueue(projectId, {
    taskType: "depth",
    wrongType: "该项目不是深度图标注任务。",
  });
  const { ready, item, submitting, submitError } = queue;
  const history = useEditHistory<RasterBuffer>(HISTORY_LIMIT);
  useResetOnItem(item ? itemText(item, "item_id") : "", () => {
    setRaster(null);
    history.clear();
  });
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

  const sign = tool === "lower" ? -1 : 1;

  const layer: ImageCanvasLayer = useMemo(
    () => ({
      id: "depth-raster",
      blendMode: "multiply",
      render(context) {
        if (!raster) return;
        drawRaster(context, raster);
      },
    }),
    [raster],
  );

  function handlePointer(event: ImageCanvasPointerEvent) {
    if (!raster) return;
    if (event.phase === "down") history.record(cloneRasterBuffer(raster));
    paint(event, radius, (value) => value + sign * strength);
  }

  function restore(step: (current: RasterBuffer) => RasterBuffer | undefined) {
    if (!raster) return;
    const previous = step(cloneRasterBuffer(raster));
    if (previous) setRaster(previous);
  }
  const undo = () => restore(history.undo);
  const redo = () => restore(history.redo);

  async function submit() {
    if (!item || !raster) return;
    await queue.submit(itemText(item, "item_id"), {
      image_size: { width: raster.width, height: raster.height },
      pixels: toBase64(raster),
    });
  }

  const resize = (delta: number) =>
    setRadius((current) => Math.min(MAX_RADIUS, Math.max(MIN_RADIUS, current + delta)));

  const hotkeys: Hotkey[] = [
    { keys: ["h"], display: "H", description: "拖动画布", run: () => setTool("pan") },
    { keys: ["1"], display: "1", description: "提高深度", run: () => setTool("raise") },
    { keys: ["2"], display: "2", description: "降低深度", run: () => setTool("lower") },
    {
      keys: ["x"],
      display: "X",
      description: "切换提高 / 降低",
      run: () => setTool((current) => (current === "raise" ? "lower" : "raise")),
    },
    { keys: ["["], display: "[", description: "缩小画笔", repeat: true, run: () => resize(-4) },
    { keys: ["]"], display: "]", description: "放大画笔", repeat: true, run: () => resize(4) },
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
        doneDescription="当前没有待标注图像，所有结果均已原子写入本地灰度图。"
        doneTitle="深度图标注已完成"
        loading="正在读取深度队列…"
        projectId={projectId}
        queue={queue}
      />
    );
  }

  return (
    <TaskWorkspace
      fileName={imagePath}
      footer={
        <SubmitBar
          disabled={!raster}
          error={submitError}
          onSubmit={() => void submit()}
          submitting={submitting}
        />
      }
      hints={CANVAS_HINTS}
      hotkeys={hotkeys}
      history={{ canUndo: history.canUndo, canRedo: history.canRedo, undo, redo }}
      panel={
        <PanelSection title="深度图">
          {!baselinePath && (
            <p className="submit-hint">未找到基线深度图，已从中灰度（128）开始编辑。</p>
          )}
          <p className="panel-note">
            深度图以正片叠底叠加在原图上：越暗表示数值越小。用右侧工具栏的“提高 / 降低”画笔在图上涂抹，
            画笔半径与强度在工具旁调整。
          </p>
          <p className="panel-note">当前：{tool === "pan" ? "拖动画布" : tool === "raise" ? "提高深度" : "降低深度"}</p>
        </PanelSection>
      }
      progress={summaryProgress(ready.project.summary, ready.queue.total)}
      projectId={projectId}
      projectName={ready.project.name}
      remaining={ready.queue.total}
      stage={
        imageSize && imageUrl && raster ? (
          <ImageCanvas
            alt={imagePath ?? ""}
            imageSize={imageSize}
            interactionMode={tool === "pan" ? "pan" : "tool"}
            layers={[layer]}
            onPointerEvent={handlePointer}
            onViewportChange={(next) => {
              viewportRef.current = next;
            }}
            src={imageUrl}
          />
        ) : (
          <LoadingState>正在读取基线深度图…</LoadingState>
        )
      }
      strip={<ImageStrip dirty={history.canUndo} projectId={projectId} queue={queue} />}
      title="深度图标注"
      toolOptions={
        tool === "pan" ? undefined : (
          <>
            <RangeField
              label="画笔半径"
              max={MAX_RADIUS}
              min={MIN_RADIUS}
              onChange={setRadius}
              unit="px"
              value={radius}
            />
            <RangeField label="调整强度" max={64} min={1} onChange={setStrength} value={strength} />
          </>
        )
      }
      tools={[
        [{ id: "pan", label: "拖动画布", icon: <HandIcon />, shortcut: "H", active: tool === "pan", onSelect: () => setTool("pan") }],
        [
          { id: "raise", label: "提高深度", icon: <RaiseIcon />, shortcut: "1", active: tool === "raise", onSelect: () => setTool("raise") },
          { id: "lower", label: "降低深度", icon: <LowerIcon />, shortcut: "2", active: tool === "lower", onSelect: () => setTool("lower") },
        ],
      ]}
    />
  );
}
