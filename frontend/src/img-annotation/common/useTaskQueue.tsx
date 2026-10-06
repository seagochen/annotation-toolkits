import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";

import {
  getProject,
  getQueue,
  submitAnnotation,
  type ProjectDetail,
  type QueueResponse,
} from "../../api/client";
import { ErrorState, LoadingState } from "../../components/AsyncState";
import { CompleteState } from "./workspace/TaskWorkspace";

export type QueueItem = QueueResponse["items"][number];
/** Which queue page is shown: the next pending item, or one already annotated. */
export type QueueView = Readonly<{ status: "pending" | "annotated"; offset: number }>;
const PENDING: QueueView = { status: "pending", offset: 0 };
export type ReadyQueue = Readonly<{ project: ProjectDetail; queue: QueueResponse }>;
type QueueState =
  | { kind: "loading" }
  | ({ kind: "ready" } & ReadyQueue)
  | { kind: "error"; message: string };

/** A queue item field as display text; missing values become "". */
export function itemText(item: QueueItem, key: string): string {
  const value = item[key];
  return value == null ? "" : String(value);
}

/** A string list from the project summary (labels, categories), else []. */
export function summaryStrings(project: ProjectDetail, key: string): string[] {
  const value = project.summary[key];
  return Array.isArray(value)
    ? value.filter((entry): entry is string => typeof entry === "string")
    : [];
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : "未知错误";
}

/**
 * The load → annotate → submit → refresh loop every task page runs.
 *
 * Loads the project and its pending queue together, refuses a project of
 * another task type, and after a successful submit re-reads the queue so the
 * next item comes from the server. `onLoad` resets page state for a freshly
 * loaded project; it may change between renders without reloading.
 */
export function useTaskQueue(
  projectId: string,
  options: { taskType: string; wrongType: string; onLoad?: (project: ProjectDetail) => void },
) {
  const { taskType, wrongType } = options;
  const [state, setState] = useState<QueueState>({ kind: "loading" });
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const onLoad = useRef(options.onLoad);
  onLoad.current = options.onLoad;
  const busy = useRef(false);
  // Only the latest navigation may replace the displayed item. A slow older
  // response must not reset edits made after a newer navigation completed.
  const requestVersion = useRef(0);
  const [view, setView] = useState<QueueView>(PENDING);
  const viewRef = useRef(view);
  viewRef.current = view;
  // Items saved during this visit, so the image list can tick them off
  // without re-reading every page of it after each save.
  const [savedIds, setSavedIds] = useState<ReadonlySet<string>>(() => new Set());
  // The page's `useResetOnItem` handler. Every write of a new queue page calls
  // it in the same batch, so the page's reset is an ordinary state update of
  // that batch rather than an update during render (see `useResetOnItem`).
  const itemListener = useRef<((item: QueueItem | undefined) => void) | null>(null);
  const shownState = useRef(state);
  shownState.current = state;

  /** Replace the queue state and let the page reset for the item it now shows. */
  const show = useCallback((next: QueueState) => {
    shownState.current = next;
    setState(next);
    itemListener.current?.(next.kind === "ready" ? next.queue.items[0] : undefined);
  }, []);
  /** Show another queue page of the already loaded project. */
  const showQueue = useCallback(
    (queue: QueueResponse) => {
      const current = shownState.current;
      if (current.kind === "ready") show({ ...current, queue });
    },
    [show],
  );

  const reload = useCallback(async () => {
    if (busy.current) return;
    const version = ++requestVersion.current;
    show({ kind: "loading" });
    setSubmitError("");
    try {
      const current = viewRef.current;
      const [project, queue] = await Promise.all([
        getProject(projectId),
        getQueue(projectId, current.status, current.offset),
      ]);
      if (version !== requestVersion.current) return;
      if (project.task_type !== taskType) {
        show({ kind: "error", message: wrongType });
        return;
      }
      onLoad.current?.(project);
      show({ kind: "ready", project, queue });
    } catch (error) {
      if (version !== requestVersion.current) return;
      show({ kind: "error", message: message(error) });
    }
  }, [projectId, show, taskType, wrongType]);

  useEffect(() => {
    void reload();
    return () => { requestVersion.current += 1; };
  }, [reload]);

  /** Save one result; resolves true once saved and the queue re-read. */
  const submit = useCallback(
    async (itemId: string, result: Record<string, unknown>): Promise<boolean> => {
      if (busy.current) return false;
      busy.current = true;
      // Invalidate a pending browse before saving; navigation is blocked until
      // the save and queue refresh finish, so pending offsets stay consistent.
      requestVersion.current += 1;
      setSubmitting(true);
      setSubmitError("");
      try {
        await submitAnnotation(projectId, itemId, result);
        setSavedIds((current) => new Set(current).add(itemId));
        let next = viewRef.current;
        let queue = await getQueue(projectId, next.status, next.offset);
        if (!queue.items.length && queue.total > 0 && next.offset > 0) {
          // Saved the last item after a jump into the middle of the queue:
          // carry on from the first one still left.
          next = { status: next.status, offset: 0 };
          queue = await getQueue(projectId, next.status, next.offset);
          setView(next);
          viewRef.current = next;
        }
        showQueue(queue);
        return true;
      } catch (error) {
        setSubmitError(message(error));
        return false;
      } finally {
        busy.current = false;
        setSubmitting(false);
      }
    },
    [projectId, showQueue],
  );

  /**
   * Show another queue page without reloading the project: the pending
   * queue, or the `offset`-th annotated item (for task types whose results
   * stay editable).
   */
  const browse = useCallback(
    async (next: QueueView): Promise<void> => {
      if (busy.current) return;
      const version = ++requestVersion.current;
      setSubmitError("");
      try {
        const queue = await getQueue(projectId, next.status, next.offset);
        if (version !== requestVersion.current) return;
        setView(next);
        viewRef.current = next;
        showQueue(queue);
      } catch (error) {
        if (version !== requestVersion.current) return;
        show({ kind: "error", message: message(error) });
      }
    },
    [projectId, show, showQueue],
  );

  const ready: ReadyQueue | null = state.kind === "ready" ? state : null;
  return {
    state,
    ready,
    item: ready?.queue.items[0] as QueueItem | undefined,
    reload,
    submit,
    submitting,
    submitError,
    view,
    browse,
    savedIds,
    itemListener,
  };
}

export type TaskQueue = ReturnType<typeof useTaskQueue>;

/**
 * What a task page shows instead of its workspace: loading, an error with
 * retry, or the finished-queue screen.
 */
export function QueueFallback({
  queue,
  projectId,
  loading,
  doneTitle,
  doneDescription,
}: {
  queue: TaskQueue;
  projectId: string;
  loading: string;
  doneTitle: string;
  doneDescription: string;
}): ReactElement {
  if (queue.state.kind === "loading") return <LoadingState>{loading}</LoadingState>;
  if (queue.state.kind === "error") {
    return <ErrorState message={queue.state.message} onRetry={() => void queue.reload()} />;
  }
  return <CompleteState description={doneDescription} projectId={projectId} title={doneTitle} />;
}

/**
 * Run `reset(item)` whenever the queue shows an item whose `keyOf` differs
 * from the last one (and with `undefined` while loading, on an error or when
 * the queue is empty), so each item starts from fresh page state.
 *
 * The queue calls it in the same batch as it stores the new item, so the
 * first render of a new item already has the reset state and the reset is
 * never recorded as an undoable edit. It deliberately does not reset during
 * render: React 18 drops an update made during render when an earlier
 * no-op update of the same state (a pointer move over the canvas) is still
 * pending, and the page would then show the previous item's shapes on the
 * new image.
 */
export function useResetOnItem(
  queue: TaskQueue,
  keyOf: (item: QueueItem) => string,
  reset: (item: QueueItem | undefined) => void,
) {
  const latest = useRef({ keyOf, reset });
  latest.current = { keyOf, reset };
  const shownKey = useRef("");
  queue.itemListener.current = (item) => {
    const key = item ? latest.current.keyOf(item) : "";
    if (key === shownKey.current) return;
    shownKey.current = key;
    latest.current.reset(item);
  };
}
