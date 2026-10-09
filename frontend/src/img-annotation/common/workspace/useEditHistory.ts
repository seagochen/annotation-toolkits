import { useCallback, useEffect, useRef, useState } from "react";

export type EditHistory<T> = Readonly<{
  canUndo: boolean;
  canRedo: boolean;
  /** Remember `snapshot` as the state to return to on the next undo. */
  record: (snapshot: T) => void;
  /** The state to restore, given the current one (kept for redo); undefined when there is none. */
  undo: (current: T) => T | undefined;
  redo: (current: T) => T | undefined;
  clear: () => void;
}>;

/**
 * Undo/redo stacks of whole editing states for one item. Pages either call
 * `record(before)` ahead of each edit (raster buffers, whose pixels change in
 * place and so must be cloned first) or let `useRecordChanges` record every
 * settled value of an immutable document (boxes, polygons).
 */
export function useEditHistory<T>(limit = 50): EditHistory<T> {
  const past = useRef<T[]>([]);
  const future = useRef<T[]>([]);
  const [, setVersion] = useState(0);
  const bump = useCallback(() => setVersion((version) => version + 1), []);

  const record = useCallback(
    (snapshot: T) => {
      past.current = [...past.current, snapshot].slice(-limit);
      future.current = [];
      bump();
    },
    [bump, limit],
  );

  const undo = useCallback(
    (current: T) => {
      const previous = past.current[past.current.length - 1];
      if (previous === undefined) return undefined;
      past.current = past.current.slice(0, -1);
      future.current = [...future.current, current];
      bump();
      return previous;
    },
    [bump],
  );

  const redo = useCallback(
    (current: T) => {
      const next = future.current[future.current.length - 1];
      if (next === undefined) return undefined;
      future.current = future.current.slice(0, -1);
      past.current = [...past.current, current].slice(-limit);
      bump();
      return next;
    },
    [bump, limit],
  );

  const clear = useCallback(() => {
    if (!past.current.length && !future.current.length) return;
    past.current = [];
    future.current = [];
    bump();
  }, [bump]);

  return {
    canUndo: past.current.length > 0,
    canRedo: future.current.length > 0,
    record,
    undo,
    redo,
    clear,
  };
}

/**
 * Record each settled value of an immutable document: whenever `value`
 * changes to a new non-null value, the previous settled one goes on the undo
 * stack. Pass null while an edit is in progress (a drag) so the whole drag
 * is one step. Values restored by undo/redo are not recorded again as long
 * as the page passes `applied` the value it restored before setting it.
 * `resetKey` starts a fresh history (a new item). `equals` decides when two
 * values are the same document (default: identity); a page whose document is
 * built from several lists compares those lists instead.
 */
export function useRecordChanges<T>(
  history: EditHistory<T>,
  value: T | null,
  resetKey: string,
  equals: (a: T, b: T) => boolean = Object.is,
) {
  const settled = useRef<{ key: string; value: T } | null>(null);
  const skip = useRef<T | null>(null);
  const { record, clear } = history;

  useEffect(() => {
    if (value === null) return;
    const previous = settled.current;
    settled.current = { key: resetKey, value };
    if (!previous || previous.key !== resetKey) {
      clear();
      return;
    }
    if (equals(previous.value, value)) return;
    if (skip.current !== null && equals(skip.current, value)) {
      skip.current = null;
      return;
    }
    record(previous.value);
    // `equals` is meant to be a module-level function, so it is not a dependency.
  }, [clear, record, resetKey, value]);

  return useCallback((applied: T) => {
    skip.current = applied;
  }, []);
}
