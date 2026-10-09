import { useCallback, useEffect, useRef, useState } from "react";

import type { TaskQueue } from "./useTaskQueue";

const SAVE_DELAY_MS = 600;

type Doc = {
  /** `itemKey` this document belongs to. */
  key: string;
  itemId: string;
  /** Content as loaded; undefined until the item's first content arrives. */
  loaded: string | undefined;
  /** Content as last saved as a draft (or loaded). */
  saved: string | undefined;
};

export type AutoSave = Readonly<{
  /** Why the last draft save failed; "" when it did not. */
  error: string;
  /** Whether the content is still what the item was loaded with. */
  pristine: boolean;
  /** Save the draft now (the "重试" of a failed save). */
  retry: () => void;
  /**
   * Drop a pending draft save and wait for a running one: call before
   * submitting, so no draft is written after the submission replaced it.
   */
  settle: () => Promise<void>;
}>;

/**
 * Autosave the shown item's edits as a draft (`useTaskQueue().saveDraft`):
 * a working copy kept on the server, not a submission — the item stays
 * pending until the page submits it ("加入数据集").
 *
 * - `content` is the result to compare (serialized), or null while it is not
 *   ready to save (an edit in progress, the image size still unknown). The
 *   first content of an item is what was loaded, so it is not saved again.
 * - A changed content is saved `SAVE_DELAY_MS` after it settles; one save
 *   runs at a time, a change made meanwhile is saved right after it.
 * - Before the queue shows another item, a pending draft is saved first;
 *   when that fails, the item stays shown with the error.
 * - Leaving the page saves a pending draft without waiting.
 */
export function useAutoSave(
  queue: TaskQueue,
  {
    itemKey,
    itemId,
    content,
    build,
  }: {
    itemKey: string;
    itemId: string;
    content: string | null;
    /** The result to save as the draft. */
    build: () => Record<string, unknown> | undefined;
  },
): AutoSave {
  const [error, setError] = useState("");
  const doc = useRef<Doc | null>(null);
  if (doc.current?.key !== itemKey) {
    doc.current = itemKey ? { key: itemKey, itemId, loaded: undefined, saved: undefined } : null;
  }
  const latest = useRef({ content, build });
  latest.current = { content, build };
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const running = useRef<Promise<boolean> | null>(null);
  const { saveDraft } = queue;

  const clearTimer = () => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
  };

  /** Save the current content as the draft if it differs from the saved one. */
  const run = useCallback((): Promise<boolean> => {
    clearTimer();
    if (running.current) {
      // Save again once the running save is done, with whatever is current then.
      return running.current.then(() => run());
    }
    const target = doc.current;
    const current = latest.current.content;
    if (!target || current === null || current === target.saved) return Promise.resolve(true);
    const result = latest.current.build();
    if (!result) return Promise.resolve(true);
    const pending = saveDraft(target.itemId, result)
      .then(() => {
        target.saved = current;
        if (doc.current === target) setError("");
        return true;
      })
      .catch((reason: unknown) => {
        if (doc.current === target) setError(reason instanceof Error ? reason.message : "未知错误");
        return false;
      })
      .finally(() => {
        running.current = null;
      });
    running.current = pending;
    return pending;
  }, [saveDraft]);

  // The first content of an item is the baseline; later changes are saved after a pause.
  useEffect(() => {
    const target = doc.current;
    if (!target || content === null) return;
    if (target.loaded === undefined) {
      target.loaded = content;
      target.saved = content;
      return;
    }
    if (content === target.saved) return;
    clearTimer();
    timer.current = setTimeout(() => void run(), SAVE_DELAY_MS);
  }, [content, itemKey, run]);

  // A failed save belongs to its item.
  useEffect(() => setError(""), [itemKey]);

  // Leaving the item: save the pending draft first.
  useEffect(() => {
    queue.leaveGuard.current = run;
  });
  useEffect(() => {
    const guard = queue.leaveGuard;
    return () => {
      guard.current = null;
      // Leaving the page: start the pending save; nothing is left to wait for it.
      if (timer.current) void run();
    };
  }, [queue.leaveGuard, run]);

  const target = doc.current;
  return {
    error,
    pristine: !target || target.loaded === undefined || content === null || content === target.loaded,
    retry: () => void run(),
    settle: async () => {
      clearTimer();
      if (running.current) await running.current;
    },
  };
}
