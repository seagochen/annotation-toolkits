import { useMemo, useRef, useState, type CSSProperties, type MouseEvent } from "react";
import { useParams } from "wouter";

import {
  CodePointIndex,
  addSpan,
  innermostSpan,
  relabelSpan,
  removeSpan,
  segmentText,
  selectionToRange,
  type Span,
} from "../../components/text-span/text-span";
import {
  OptionList,
  PanelSection,
  SubmitBar,
  TaskWorkspace,
} from "../../components/workspace/TaskWorkspace";
import { categoryColor } from "../../components/workspace/palette";
import { DIGIT_KEYS, MOD_LABEL, SAVE_KEYS, useHotkeys, type Hotkey } from "../../components/workspace/useHotkeys";
import { summaryProgress } from "../../project-meta";
import { TextDocument } from "../ItemStage";
import { QueueFallback, itemText, summaryStrings, useTaskQueue } from "../useTaskQueue";
import "./text-span.css";

const MAX_STACKED_MARKS = 4;

/** UTF-16 offset of a DOM position inside the document element. */
function offsetWithin(root: HTMLElement, node: Node, offset: number): number | null {
  if (!root.contains(node)) return null;
  const range = document.createRange();
  range.setStart(root, 0);
  range.setEnd(node, offset);
  return range.toString().length;
}

/**
 * Marks for the spans covering a segment: the innermost span tints the
 * background, and every covering span adds one coloured underline, so
 * nested and overlapping spans stay visible at once.
 */
function segmentStyle(spans: readonly Span[], covering: readonly number[], labels: readonly string[]): CSSProperties {
  const color = (index: number) => categoryColor(labels.indexOf(spans[index].label));
  const inner = innermostSpan(spans, covering);
  const lines = covering.slice(0, MAX_STACKED_MARKS).map((index, depth) => `0 ${2 + depth * 3}px 0 ${color(index)}`);
  return {
    background: inner === null ? undefined : `${color(inner)}2e`,
    boxShadow: lines.join(", "),
    paddingBottom: `${covering.length > 1 ? Math.min(covering.length, MAX_STACKED_MARKS) * 3 - 2 : 0}px`,
  };
}

export function TextSpanReviewPage() {
  const { projectId = "" } = useParams();
  const [spans, setSpans] = useState<Span[]>([]);
  const [label, setLabel] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const documentRef = useRef<HTMLDivElement>(null);
  // The click that follows a drag's mouseup must not undo what the drag did.
  const dragged = useRef(false);

  const queue = useTaskQueue(projectId, {
    taskType: "text_span",
    wrongType: "该项目不是文本片段标注任务。",
    onLoad: (project) => {
      setSpans([]);
      setSelected(null);
      setLabel(summaryStrings(project, "labels")[0] ?? "");
    },
  });
  const { ready, item, submitting, submitError } = queue;
  const labels = useMemo(() => (ready ? summaryStrings(ready.project, "labels") : []), [ready]);
  const text = item && typeof item.text === "string" ? item.text : null;
  const textError = item?.text_error == null ? null : String(item.text_error);
  const index = useMemo(() => new CodePointIndex(text ?? ""), [text]);
  const segments = useMemo(() => segmentText(index, spans), [index, spans]);

  function chooseLabel(next: string) {
    setLabel(next);
    // With a span selected, picking a label relabels that span.
    if (selected !== null) {
      const target = spans[selected];
      const updated = relabelSpan(spans, selected, next, labels);
      setSpans(updated);
      const position = updated.findIndex(
        (span) => span.start === target.start && span.end === target.end && span.label === next,
      );
      setSelected(position === -1 ? null : position);
    }
  }

  /** A finished drag-selection becomes a span with the current label. */
  function handleMouseUp() {
    const root = documentRef.current;
    const selection = window.getSelection();
    dragged.current = false;
    if (!root || !selection || selection.rangeCount === 0 || selection.isCollapsed || !label) return;
    dragged.current = true;
    const anchor = selection.anchorNode && offsetWithin(root, selection.anchorNode, selection.anchorOffset);
    const focus = selection.focusNode && offsetWithin(root, selection.focusNode, selection.focusOffset);
    if (anchor == null || focus == null) return;
    const range = selectionToRange(index, anchor, focus);
    selection.removeAllRanges();
    if (!range) return;
    const span = { ...range, label };
    const next = addSpan(spans, span, labels);
    setSpans(next);
    setSelected(next.findIndex((entry) => entry.start === span.start && entry.end === span.end && entry.label === label));
  }

  /** A plain click on marked text selects its innermost span. */
  function handleSegmentClick(event: MouseEvent, covering: readonly number[]) {
    event.stopPropagation();
    if (dragged.current) return;
    setSelected(innermostSpan(spans, covering));
  }

  function handleDocumentClick() {
    if (!dragged.current) setSelected(null);
  }

  function deleteSelected() {
    if (selected === null) return;
    setSpans((current) => removeSpan(current, selected));
    setSelected(null);
  }

  async function submit() {
    if (!item || text === null) return;
    if (await queue.submit(itemText(item, "item_id"), { spans })) {
      setSpans([]);
      setSelected(null);
    }
  }

  const hotkeys: Hotkey[] = [
    {
      keys: DIGIT_KEYS,
      display: "1–9",
      description: "选择标签（选中片段时改为该标签）",
      run: (key) => {
        const next = labels[Number(key) - 1];
        if (next) chooseLabel(next);
      },
    },
    { keys: ["delete", "backspace"], display: "Del", description: "删除选中的片段", run: deleteSelected },
    { keys: ["escape"], display: "Esc", description: "取消选中", run: () => setSelected(null) },
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
        doneDescription="当前没有待标注文本，所有结果均已原子写入本地 JSON。"
        doneTitle="文本片段标注已完成"
        loading="正在读取文本队列…"
        projectId={projectId}
        queue={queue}
      />
    );
  }

  const path = itemText(item, "image_path");
  const counts: Record<string, number> = {};
  for (const span of spans) counts[span.label] = (counts[span.label] ?? 0) + 1;
  const selectedSpan = selected === null ? undefined : spans[selected];

  return (
    <TaskWorkspace
      fileName={path}
      footer={
        <SubmitBar
          disabled={text === null}
          error={submitError}
          hint="文本无法读取，不能保存"
          onSubmit={() => void submit()}
          submitting={submitting}
        />
      }
      hints={[{ display: "拖选文本", description: "以当前标签新建片段" }]}
      hotkeys={hotkeys}
      panel={
        <>
          <PanelSection title="标签">
            <OptionList
              counts={counts}
              label="片段标签"
              name="text-span-label"
              onToggle={chooseLabel}
              options={labels}
              selected={selectedSpan ? [selectedSpan.label] : [label]}
              swatches
            />
            <p className="panel-note">
              {selectedSpan
                ? "已选中一个片段：选择标签会修改它的标签。"
                : `在左侧拖选文本，新建一个「${label}」片段。片段可以重叠或嵌套。`}
            </p>
          </PanelSection>
          <PanelSection aside={<span className="project-id">{spans.length} 个</span>} title="片段">
            {spans.length === 0 ? (
              <p className="empty-note">还没有片段。没有需要标注的内容时可直接保存。</p>
            ) : (
              <ul className="item-list">
                {spans.map((span, position) => (
                  <li key={`${span.start}-${span.end}-${span.label}`}>
                    <button
                      aria-pressed={position === selected}
                      className="item-select"
                      onClick={() => setSelected(position)}
                      type="button"
                    >
                      <span
                        aria-hidden="true"
                        className="option-swatch"
                        style={{ background: categoryColor(labels.indexOf(span.label)) }}
                      />
                      <span className="span-excerpt">
                        {span.label}：{index.slice(span.start, span.end)}
                      </span>
                      <span className="item-meta">
                        {span.start}–{span.end}
                      </span>
                    </button>
                    <button
                      aria-label={`删除片段 ${span.label} ${span.start}–${span.end}`}
                      className="icon-button"
                      onClick={() => {
                        setSpans((current) => removeSpan(current, position));
                        setSelected(null);
                      }}
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
      progress={summaryProgress(ready.project.summary, ready.queue.total)}
      projectId={projectId}
      projectName={ready.project.name}
      remaining={ready.queue.total}
      stage={
        <TextDocument error={textError} path={path} text={text}>
          {text === "" ? (
            <span className="muted">（空文档）</span>
          ) : (
            <div className="span-document" onClick={handleDocumentClick} onMouseUp={handleMouseUp} ref={documentRef}>
              {segments.map((segment) =>
                segment.covering.length === 0 ? (
                  <span key={segment.start}>{segment.text}</span>
                ) : (
                  <mark
                    className={
                      selected !== null && segment.covering.includes(selected) ? "span-mark selected" : "span-mark"
                    }
                    key={segment.start}
                    onClick={(event) => handleSegmentClick(event, segment.covering)}
                    style={segmentStyle(spans, segment.covering, labels)}
                    title={segment.covering.map((position) => spans[position].label).join(" / ")}
                  >
                    {segment.text}
                  </mark>
                ),
              )}
            </div>
          )}
        </TextDocument>
      }
      title="文本片段标注"
      unit="篇"
    />
  );
}
