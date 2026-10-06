import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { listQueueItems, projectFileUrl } from "../../api/client";
import {
  ChevronLeftIcon,
  ChevronRightIcon,
  DocumentIcon,
} from "./workspace/tool-icons";
import { itemText, type QueueItem, type QueueView, type TaskQueue } from "./useTaskQueue";

const PAGE_SIZE = 200;

type ListState = Readonly<{ items: QueueItem[]; total: number; loading: boolean; error: string }>;

type Entry = Readonly<{
  id: string;
  path: string;
  isText: boolean;
  done: boolean;
  /** The queue page that shows this item, or null when it cannot be opened. */
  target: QueueView | null;
}>;

/**
 * The project's items in dataset order, read page by page from the unfiltered
 * queue (`annotated` says which are finished).
 */
function useItemList(projectId: string) {
  const [state, setState] = useState<ListState>({ items: [], total: 0, loading: true, error: "" });
  const loading = useRef(false);

  const load = useCallback(
    async (offset: number) => {
      if (loading.current) return;
      loading.current = true;
      setState((current) => ({ ...current, loading: true, error: "" }));
      try {
        const page = await listQueueItems(projectId, offset, PAGE_SIZE);
        setState((current) => ({
          items: offset === 0 ? [...page.items] : [...current.items, ...page.items],
          total: page.total,
          loading: false,
          error: "",
        }));
      } catch (error) {
        setState((current) => ({
          ...current,
          loading: false,
          error: error instanceof Error ? error.message : "未知错误",
        }));
      } finally {
        loading.current = false;
      }
    },
    [projectId],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  return { ...state, loadMore: () => void load(state.items.length) };
}

/**
 * Roboflow-style list of the project's images beside the canvas: a
 * thumbnail per item, a tick on finished ones, and "‹ N / M ›" to step
 * through them. Unfinished items open from the pending queue; finished ones
 * open only for task types whose results stay editable (`revisable`).
 */
export function ImageStrip({
  projectId,
  queue,
  revisable = false,
  dirty = false,
  title = "图像",
}: {
  title?: string;
  projectId: string;
  queue: TaskQueue;
  revisable?: boolean;
  /** Unsaved edits on the current item: confirm before leaving it. */
  dirty?: boolean;
}) {
  const list = useItemList(projectId);
  const currentId = queue.item ? itemText(queue.item, "item_id") : "";
  const listRef = useRef<HTMLUListElement>(null);

  const entries: Entry[] = useMemo(() => {
    let pending = 0;
    let annotated = 0;
    return list.items.map((item) => {
      const id = itemText(item, "item_id");
      const done = item.annotated === true || queue.savedIds.has(id);
      const target: QueueView | null = done
        ? revisable
          ? { status: "annotated", offset: annotated }
          : null
        : { status: "pending", offset: pending };
      if (done) annotated += 1;
      else pending += 1;
      return { id, path: itemText(item, "image_path"), isText: item.media === "text", done, target };
    });
  }, [list.items, queue.savedIds, revisable]);

  const position = entries.findIndex((entry) => entry.id === currentId);

  useEffect(() => {
    if (position < 0) return;
    const element = listRef.current?.children[position] as HTMLElement | undefined;
    element?.scrollIntoView?.({ block: "nearest" });
  }, [position]);

  function open(entry: Entry) {
    if (!entry.target || entry.id === currentId) return;
    if (dirty && !window.confirm("当前图像有未保存的修改，离开后将丢失。确定切换吗？")) return;
    void queue.browse(entry.target);
  }

  function step(direction: 1 | -1) {
    for (let index = position + direction; index >= 0 && index < entries.length; index += direction) {
      if (entries[index].target) {
        open(entries[index]);
        return;
      }
    }
  }

  const canStep = (direction: 1 | -1) => {
    if (position < 0) return false;
    for (let index = position + direction; index >= 0 && index < entries.length; index += direction) {
      if (entries[index].target) return true;
    }
    return false;
  };

  return (
    <aside aria-label={`${title}列表`} className="image-strip">
      <header className="image-strip-head">
        <h2>{title}</h2>
        <div className="image-strip-nav">
          <button aria-label="上一张" disabled={!canStep(-1)} onClick={() => step(-1)} type="button">
            <ChevronLeftIcon />
          </button>
          <span className="image-strip-position">
            {position >= 0 ? position + 1 : "–"} / {list.total}
          </span>
          <button aria-label="下一张" disabled={!canStep(1)} onClick={() => step(1)} type="button">
            <ChevronRightIcon />
          </button>
        </div>
      </header>
      <ul className="image-strip-list" ref={listRef}>
        {entries.map((entry) => {
          const name = entry.path.split("/").pop() || entry.path;
          const current = entry.id === currentId;
          return (
            <li key={entry.id}>
              <button
                aria-current={current ? "true" : undefined}
                aria-label={`${entry.path}${entry.done ? "（已标注）" : ""}`}
                className="image-strip-item"
                disabled={!entry.target && !current}
                onClick={() => open(entry)}
                title={entry.target || current ? entry.path : `${entry.path}：已提交，该任务的结果不可修改`}
                type="button"
              >
                <span className="image-strip-thumb">
                  {entry.isText ? (
                    <DocumentIcon />
                  ) : (
                    <img alt="" decoding="async" loading="lazy" src={projectFileUrl(projectId, entry.path)} />
                  )}
                  {entry.done && (
                    <span aria-hidden="true" className="image-strip-check">
                      ✓
                    </span>
                  )}
                </span>
                <span className="image-strip-name">{name}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {list.error && (
        <p className="image-strip-note" role="alert">
          {list.error}
        </p>
      )}
      {list.loading && <p className="image-strip-note">正在读取…</p>}
      {!list.loading && list.items.length < list.total && (
        <button className="image-strip-more" onClick={list.loadMore} type="button">
          加载更多（{list.items.length} / {list.total}）
        </button>
      )}
    </aside>
  );
}
