import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "wouter";

import { projectFileUrl } from "../../../api/client";
import { ErrorState, LoadingState } from "../../../components/AsyncState";
import {
  ImageCanvas,
  type ImageCanvasLayer,
  type ImageCanvasPointerEvent,
  type Point,
  type Viewport,
} from "../../common/image-canvas";
import {
  MIN_POLYGON_POINTS,
  beginOrExtendDraft,
  cancelDraft,
  clampPoint,
  closeDraft,
  createPolygonToolState,
  createPolygons,
  deleteVertex,
  dragVertexTo,
  endVertexDrag,
  pointerDownEdit,
  relabelPolygon,
  removePolygon,
  selectPolygon,
  setCategory,
  undoLastPoint,
  type Polygon,
  type PolygonToolState,
} from "../../common/image-canvas/polygon-tool";
import {
  AnnotationTabs,
  CANVAS_HINTS,
  CompleteState,
  OptionList,
  PanelSection,
  Segmented,
  SubmitBar,
  TaskWorkspace,
} from "../../common/workspace/TaskWorkspace";
import { categoryColor } from "../../common/workspace/palette";
import { HandIcon, PolygonIcon, SelectIcon } from "../../common/workspace/tool-icons";
import { useEditHistory, useRecordChanges } from "../../common/workspace/useEditHistory";
import {
  DIGIT_KEYS,
  MOD_LABEL,
  REDO_KEYS,
  SAVE_KEYS,
  UNDO_KEYS,
  useHotkeys,
  type Hotkey,
} from "../../common/workspace/useHotkeys";
import { summaryProgress } from "../../../project-meta";
import { ImageStrip } from "../../common/ImageStrip";
import { useImageSize } from "../../common/useImageSize";
import { itemText, summaryStrings, useResetOnItem, useTaskQueue, type QueueItem } from "../../common/useTaskQueue";

const HANDLE_SCREEN_SIZE = 8;
const HIT_SCREEN_TOLERANCE = 7;

type Mode = "edit" | "pan" | "draw";
type StoredPolygon = { category: string; points: [number, number][] };

const SOURCE_LABELS: Record<string, string> = {
  annotation: "已提交的结果",
  prelabel: "COCO 预标",
  none: "空白",
};

function storedPolygons(item: QueueItem): StoredPolygon[] {
  return Array.isArray(item.polygons) ? (item.polygons as StoredPolygon[]) : [];
}

function storedSize(item: QueueItem): { width: number; height: number } | null {
  const size = item.image_size as { width?: unknown; height?: unknown } | null | undefined;
  return size && typeof size.width === "number" && typeof size.height === "number"
    ? { width: size.width, height: size.height }
    : null;
}

/** Two decimals is far below a pixel and keeps the JSON readable. */
function round(value: number): number {
  return Math.round(value * 100) / 100;
}

function polygonArea(points: readonly Point[]): number {
  let total = 0;
  points.forEach((point, index) => {
    const next = points[(index + 1) % points.length];
    total += point.x * next.y - next.x * point.y;
  });
  return Math.abs(total) / 2;
}

export function PolygonReviewPage() {
  const { projectId = "" } = useParams();
  const [tool, setTool] = useState<PolygonToolState>(createPolygonToolState());
  const [mode, setMode] = useState<Mode>("edit");
  const [notice, setNotice] = useState("");
  const viewportRef = useRef<Viewport>({ scale: 1, offset: { x: 0, y: 0 } });

  const queue = useTaskQueue(projectId, {
    taskType: "polygon",
    wrongType: "该项目不是多边形标注任务。",
  });
  const { ready, item, submitting, submitError, view } = queue;
  const categories = useMemo(
    () => (ready ? summaryStrings(ready.project, "categories") : []),
    [ready],
  );
  const imagePath = item ? itemText(item, "image_path") : undefined;
  const imageUrl = imagePath ? projectFileUrl(projectId, imagePath) : undefined;
  const imageSize = useImageSize(imageUrl);
  const revision = item && typeof item.revision === "number" ? item.revision : 0;
  const itemKey = item ? `${itemText(item, "item_id")}:${revision}:${itemText(item, "source")}` : "";

  // Each item (and each new revision of it) starts from what the server sent;
  // `item` changes identity on every queue read, the key says when it is really new.
  useResetOnItem(itemKey, () => {
    if (!item) return;
    const polygons = createPolygons(
      storedPolygons(item).map((polygon) => ({
        category: polygon.category,
        points: polygon.points.map(([x, y]) => ({ x, y })),
      })),
    );
    setTool((current) => createPolygonToolState(polygons, current.category || categories[0] || ""));
    if (mode === "draw") setMode("edit");
    setNotice("");
  });
  const history = useEditHistory<readonly Polygon[]>();
  const markApplied = useRecordChanges(history, tool.drag ? null : tool.polygons, itemKey);

  // A browsed position that no longer exists (fewer results than before) falls back to the first.
  const { browse } = queue;
  const pastEnd = Boolean(ready && !item && view.status === "annotated" && view.offset > 0);
  useEffect(() => {
    if (pastEnd) void browse({ status: "annotated", offset: 0 });
  }, [pastEnd, browse]);

  const knownSize = item ? storedSize(item) : null;
  const sizeMismatch =
    imageSize && knownSize && (imageSize.width !== knownSize.width || imageSize.height !== knownSize.height);

  const layer: ImageCanvasLayer = useMemo(
    () => ({
      id: "polygons",
      render(context, frame) {
        const scale = frame.viewport.scale;
        const handle = HANDLE_SCREEN_SIZE / scale;
        for (const polygon of tool.polygons) {
          const selected = polygon.id === tool.selectedId;
          const color = categoryColor(categories.indexOf(polygon.category));
          context.beginPath();
          polygon.points.forEach((point, index) =>
            index === 0 ? context.moveTo(point.x, point.y) : context.lineTo(point.x, point.y),
          );
          context.closePath();
          context.fillStyle = `${color}${selected ? "55" : "33"}`;
          context.fill();
          context.lineWidth = (selected ? 3 : 2) / scale;
          context.strokeStyle = color;
          context.stroke();
          if (!selected) continue;
          polygon.points.forEach((point, index) => {
            const active = index === tool.selectedVertex;
            const size = active ? handle * 1.4 : handle;
            context.fillStyle = active ? color : "#ffffff";
            context.strokeStyle = color;
            context.lineWidth = 1.5 / scale;
            context.fillRect(point.x - size / 2, point.y - size / 2, size, size);
            context.strokeRect(point.x - size / 2, point.y - size / 2, size, size);
          });
        }
        if (tool.draft?.length) {
          const color = categoryColor(categories.indexOf(tool.category));
          context.setLineDash([6 / scale, 4 / scale]);
          context.lineWidth = 2 / scale;
          context.strokeStyle = color;
          context.beginPath();
          tool.draft.forEach((point, index) =>
            index === 0 ? context.moveTo(point.x, point.y) : context.lineTo(point.x, point.y),
          );
          context.stroke();
          context.setLineDash([]);
          for (const point of tool.draft) {
            context.beginPath();
            context.arc(point.x, point.y, 3 / scale, 0, Math.PI * 2);
            context.fillStyle = color;
            context.fill();
          }
        }
      },
    }),
    [categories, tool],
  );

  function handlePointer(event: ImageCanvasPointerEvent) {
    if (!imageSize) return;
    const tolerance = HIT_SCREEN_TOLERANCE / viewportRef.current.scale;
    const point = clampPoint(event.image, imageSize);
    if (mode === "draw") {
      if (event.phase === "down") {
        setTool((current) => beginOrExtendDraft(current, point, tolerance));
      }
      return;
    }
    if (event.phase === "down") {
      setNotice("");
      setTool((current) => pointerDownEdit(current, event.image, tolerance));
    } else if (event.phase === "move") {
      setTool((current) => (current.drag ? dragVertexTo(current, event.image, imageSize) : current));
    } else {
      setTool(endVertexDrag);
    }
  }

  function chooseCategory(next: string) {
    setTool((current) => {
      const updated = setCategory(current, next);
      // With a polygon selected, picking a category relabels it.
      return current.selectedId && mode === "edit" ? relabelPolygon(updated, current.selectedId, next) : updated;
    });
  }

  function toggleDraw() {
    setNotice("");
    setTool((current) => selectPolygon(cancelDraft(current), null));
    setMode((current) => (current === "draw" ? "edit" : "draw"));
  }

  function finishDraft() {
    setTool((current) => {
      if (!current.draft) return current;
      const closed = closeDraft(current);
      if (closed.polygons.length === current.polygons.length) return current;
      return selectPolygon(closed, closed.polygons[closed.polygons.length - 1].id);
    });
    setMode("edit");
  }

  function deleteSelection() {
    const polygon = tool.polygons.find((candidate) => candidate.id === tool.selectedId);
    if (!polygon) return;
    if (tool.selectedVertex !== null) {
      if (polygon.points.length <= MIN_POLYGON_POINTS) {
        setNotice(`多边形至少需要 ${MIN_POLYGON_POINTS} 个顶点；要去掉整个多边形，请先取消选中顶点或用列表中的 ×。`);
        return;
      }
      setTool((current) => deleteVertex(current, polygon.id, current.selectedVertex ?? -1));
      return;
    }
    setTool((current) => removePolygon(current, polygon.id));
  }

  const result = imageSize
    ? {
        image_size: imageSize,
        base_revision: revision,
        polygons: tool.polygons.map((polygon: Polygon) => ({
          category: polygon.category,
          points: polygon.points.map((point) => [round(point.x), round(point.y)]),
        })),
      }
    : undefined;

  async function submit() {
    if (!item || !result || submitting) return;
    if (tool.draft?.length) {
      setNotice("还有未闭合的多边形：按 Enter 闭合，或按 Esc 放弃后再保存。");
      return;
    }
    await queue.submit(itemText(item, "item_id"), result);
  }

  function restore(step: (current: readonly Polygon[]) => readonly Polygon[] | undefined) {
    const polygons = step(tool.polygons);
    if (!polygons) return;
    markApplied(polygons);
    setNotice("");
    setTool((current) => ({ ...current, polygons, selectedId: null, selectedVertex: null, drag: null }));
  }
  function undo() {
    // While drawing, undo takes back the draft's last point first.
    if (tool.draft?.length) setTool(undoLastPoint);
    else restore(history.undo);
  }
  const redo = () => restore(history.redo);

  function chooseMode(next: Mode) {
    setNotice("");
    if (next !== "draw") setTool(cancelDraft);
    else setTool((current) => selectPolygon(current, null));
    setMode(next);
  }

  const hotkeys: Hotkey[] = [
    {
      keys: DIGIT_KEYS,
      display: "1–9",
      description: "选择类别（选中多边形时改为该类别）",
      run: (key) => {
        const next = categories[Number(key) - 1];
        if (next) chooseCategory(next);
      },
    },
    { keys: ["v"], display: "V", description: "选择 / 编辑顶点", run: () => chooseMode("edit") },
    { keys: ["h"], display: "H", description: "拖动画布", run: () => chooseMode("pan") },
    { keys: ["p"], display: "P", description: "绘制新多边形 / 回到编辑", run: toggleDraw },
    { keys: ["enter"], display: "Enter", description: "闭合正在绘制的多边形", run: finishDraft },
    {
      keys: ["backspace"],
      display: "Backspace",
      description: "绘制时撤销最后一点；编辑时同 Del",
      repeat: true,
      run: () => (mode === "draw" ? setTool(undoLastPoint) : deleteSelection()),
    },
    { keys: ["delete"], display: "Del", description: "删除选中的顶点（未选顶点时删除多边形）", run: deleteSelection },
    {
      keys: ["escape"],
      display: "Esc",
      description: "放弃正在绘制的多边形 / 取消选中",
      run: () => {
        setNotice("");
        if (mode === "draw") {
          setTool(cancelDraft);
          setMode("edit");
        } else {
          setTool((current) => selectPolygon(current, null));
        }
      },
    },
    { keys: UNDO_KEYS, display: `${MOD_LABEL} + Z`, description: "撤销（绘制时撤销一点）", run: undo },
    { keys: REDO_KEYS, display: `${MOD_LABEL} + Shift + Z`, description: "重做", run: redo },
    {
      keys: SAVE_KEYS,
      display: `${MOD_LABEL} + Enter`,
      description: "保存并继续",
      run: () => void submit(),
    },
  ];
  useHotkeys(hotkeys, Boolean(item));

  if (queue.state.kind === "loading") return <LoadingState>正在读取多边形队列…</LoadingState>;
  if (queue.state.kind === "error") {
    return <ErrorState message={queue.state.message} onRetry={() => void queue.reload()} />;
  }
  if (!ready) return <LoadingState>正在读取多边形队列…</LoadingState>;

  const viewSwitch = (
    <Segmented
      label="队列"
      onChange={(status) => void queue.browse({ status, offset: 0 })}
      options={[
        { value: "pending", label: "待标注" },
        { value: "annotated", label: "已提交" },
      ]}
      value={view.status}
    />
  );

  if (!item) {
    if (pastEnd) return <LoadingState>正在读取多边形队列…</LoadingState>;
    return (
      <div className="page">
        <CompleteState
          description={
            view.status === "pending"
              ? "当前没有待标注图像，所有结果均已原子写入本地 JSON。已提交的结果仍可在“已提交”中修改。"
              : "还没有已提交的结果。"
          }
          projectId={projectId}
          title={view.status === "pending" ? "多边形标注已完成" : "没有已提交的结果"}
        />
        <div className="queue-switch-standalone">{viewSwitch}</div>
      </div>
    );
  }

  const counts: Record<string, number> = {};
  for (const polygon of tool.polygons) counts[polygon.category] = (counts[polygon.category] ?? 0) + 1;
  const selected = tool.polygons.find((polygon) => polygon.id === tool.selectedId);
  const source = itemText(item, "source");
  const browsing = view.status === "annotated";

  return (
    <TaskWorkspace
      fileName={imagePath}
      footer={
        <SubmitBar
          disabled={!imageSize}
          error={submitError}
          onSubmit={() => void submit()}
          submitting={submitting}
        >
          <button className="button-secondary" onClick={() => void queue.reload()} type="button">
            重新载入
          </button>
        </SubmitBar>
      }
      hints={[
        { display: "拖动顶点", description: "移动顶点" },
        { display: "点击选中多边形的边", description: "在该处插入顶点并可拖动" },
        ...CANVAS_HINTS,
      ]}
      hotkeys={hotkeys}
      panel={
        <>
          <PanelSection title="队列">
            {viewSwitch}
            <p className="panel-note">
              {browsing
                ? `已提交 ${view.offset + 1} / ${ready.queue.total}，第 ${revision} 版。保存会生成新版本，旧版本保留在历史中。`
                : `初始内容：${SOURCE_LABELS[source] ?? source}。`}
            </p>
            {sizeMismatch && knownSize && (
              <p className="inline-error" role="alert">
                图片实际尺寸 {imageSize?.width}×{imageSize?.height} 与{source === "prelabel" ? "预标" : "已保存结果"}的
                {knownSize.width}×{knownSize.height} 不一致，保存会被拒绝；请检查预标文件的 width/height。
              </p>
            )}
          </PanelSection>
          <AnnotationTabs
            classes={
              <>
                <OptionList
                  counts={counts}
                  label="多边形类别"
                  name="polygon-category"
                  onToggle={chooseCategory}
                  options={categories}
                  selected={selected && mode === "edit" ? [selected.category] : [tool.category]}
                  swatches
                />
                {selected && mode === "edit" && <p className="panel-note">已选中一个多边形：选择类别会修改它的类别。</p>}
              </>
            }
            count={tool.polygons.length}
            layers={
              tool.polygons.length === 0 ? (
                <p className="empty-note">还没有多边形。该图没有目标时可直接保存。</p>
              ) : (
                <ul className="item-list">
                  {tool.polygons.map((polygon, index) => (
                    <li key={polygon.id}>
                      <button
                        aria-pressed={polygon.id === tool.selectedId}
                        className="item-select"
                        onClick={() => {
                          setMode("edit");
                          setTool((current) => selectPolygon(cancelDraft(current), polygon.id));
                        }}
                        type="button"
                      >
                        <span
                          aria-hidden="true"
                          className="option-swatch"
                          style={{ background: categoryColor(categories.indexOf(polygon.category)) }}
                        />
                        <span>
                          #{index + 1} {polygon.category}
                        </span>
                        <span className="item-meta">
                          {polygon.points.length} 点 · {Math.round(polygonArea(polygon.points))} px²
                        </span>
                      </button>
                      <button
                        aria-label={`删除多边形 #${index + 1}`}
                        className="icon-button"
                        onClick={() => setTool((current) => removePolygon(current, polygon.id))}
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
              ? `逐点单击勾勒一个「${tool.category}」多边形，点回起点或按 Enter 闭合；Backspace 撤销一点。`
              : mode === "pan"
                ? "拖动平移画布；按 V 回到编辑。"
                : selected
                  ? tool.selectedVertex !== null
                    ? `已选中第 ${tool.selectedVertex + 1} 个顶点：拖动移动，Del 删除。`
                    : "拖动顶点移动；点击边插入顶点；Del 删除该多边形。"
                  : "点击多边形选中它，然后编辑顶点。"}
          </p>
          {notice && (
            <p className="inline-error" role="alert">
              {notice}
            </p>
          )}
        </>
      }
      history={{ canUndo: history.canUndo || Boolean(tool.draft?.length), canRedo: history.canRedo, undo, redo }}
      progress={summaryProgress(ready.project.summary, browsing ? undefined : ready.queue.total)}
      projectId={projectId}
      projectName={ready.project.name}
      rawData={result}
      remaining={browsing ? Number(ready.project.summary.pending ?? 0) : ready.queue.total}
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
      strip={<ImageStrip dirty={history.canUndo} projectId={projectId} queue={queue} revisable />}
      title="多边形标注"
      tools={[
        [
          { id: "edit", label: "选择", icon: <SelectIcon />, shortcut: "V", active: mode === "edit", onSelect: () => chooseMode("edit") },
          { id: "pan", label: "拖动画布", icon: <HandIcon />, shortcut: "H", active: mode === "pan", onSelect: () => chooseMode("pan") },
        ],
        [{ id: "draw", label: "多边形", icon: <PolygonIcon />, shortcut: "P", active: mode === "draw", onSelect: () => chooseMode("draw") }],
      ]}
    />
  );
}
