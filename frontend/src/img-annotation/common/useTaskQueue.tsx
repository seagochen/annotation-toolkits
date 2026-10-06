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
  const [view, setView] = useState<QueueView>(PENDING);
  const viewRef = useRef(view);
  viewRef.current = view;
  // Items saved during this visit, so the image list can tick them off
  // without re-reading every page of it after each save.
  const [savedIds, setSavedIds] = useState<ReadonlySet<string>>(() => new Set());

  const reload = useCallback(async () => {
    setState({ kind: "loading" });
    setSubmitError("");
    try {
      const current = viewRef.current;
      const [project, queue] = await Promise.all([
        getProject(projectId),
        getQueue(projectId, current.status, current.offset),
      ]);
      if (project.task_type !== taskType) {
        setState({ kind: "error", message: wrongType });
        return;
      }
      onLoad.current?.(project);
      setState({ kind: "ready", project, queue });
    } catch (error) {
      setState({ kind: "error", message: message(error) });
    }
  }, [projectId, taskType, wrongType]);

  useEffect(() => {
    void reload();
  }, [reload]);

  /** Save one result; resolves true once saved and the queue re-read. */
  const submit = useCallback(
    async (itemId: string, result: Record<string, unknown>): Promise<boolean> => {
      if (busy.current) return false;
      busy.current = true;
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
        setState((current) => (current.kind === "ready" ? { ...current, queue } : current));
        return true;
      } catch (error) {
        setSubmitError(message(error));
        return false;
      } finally {
        busy.current = false;
        setSubmitting(false);
      }
    },
    [projectId],
  );

  /**
   * Show another queue page without reloading the project: the pending
   * queue, or the `offset`-th annotated item (for task types whose results
   * stay editable).
   */
  const browse = useCallback(
    async (next: QueueView): Promise<void> => {
      setSubmitError("");
      try {
        const queue = await getQueue(projectId, next.status, next.offset);
        setView(next);
        viewRef.current = next;
        setState((current) => (current.kind === "ready" ? { ...current, queue } : current));
      } catch (error) {
        setState({ kind: "error", message: message(error) });
      }
    },
    [projectId],
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
 * Run `reset` while rendering whenever `key` (the shown item) changes, so the
 * first render of a new item already has fresh page state — an effect would
 * render the previous item's shapes on the new image once, and record the
 * reset in the undo history.
 */
export function useResetOnItem(key: string, reset: () => void) {
  const [current, setCurrent] = useState(key);
  if (current !== key) {
    setCurrent(key);
    reset();
  }
}
