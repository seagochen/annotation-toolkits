import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";

import {
  getProject,
  getQueue,
  submitAnnotation,
  type ProjectDetail,
  type QueueResponse,
} from "../api/client";
import { ErrorState, LoadingState } from "../components/AsyncState";
import { CompleteState } from "../components/workspace/TaskWorkspace";

export type QueueItem = QueueResponse["items"][number];
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

  const reload = useCallback(async () => {
    setState({ kind: "loading" });
    setSubmitError("");
    try {
      const [project, queue] = await Promise.all([getProject(projectId), getQueue(projectId)]);
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
        const queue = await getQueue(projectId);
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

  const ready: ReadyQueue | null = state.kind === "ready" ? state : null;
  return {
    state,
    ready,
    item: ready?.queue.items[0] as QueueItem | undefined,
    reload,
    submit,
    submitting,
    submitError,
  };
}

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
  queue: ReturnType<typeof useTaskQueue>;
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
