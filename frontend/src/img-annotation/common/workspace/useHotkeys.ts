import { useEffect, useRef } from "react";

export type Hotkey = Readonly<{
  /**
   * `event.key`, lower-cased; prefix `mod+` for Ctrl (or ⌘ on macOS), and
   * `mod+shift+` when Shift is held as well.
   */
  keys: readonly string[];
  /** What the shortcut list shows, e.g. "1–9" or "Ctrl + Enter". */
  display: string;
  description: string;
  run: (key: string) => void;
  /** Fire even while typing in a text field (only sensible with `mod+`). */
  allowInText?: boolean;
  /** Fire on auto-repeat (holding the key); off for toggles. */
  repeat?: boolean;
}>;

const NON_TEXT_INPUTS = new Set(["radio", "checkbox", "range", "button", "submit", "reset", "color"]);

function typingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) {
    return true;
  }
  return target instanceof HTMLInputElement && !NON_TEXT_INPUTS.has(target.type);
}

function eventKey(event: KeyboardEvent): string {
  const key = event.key.toLowerCase();
  if (!event.ctrlKey && !event.metaKey) return key;
  return event.shiftKey ? `mod+shift+${key}` : `mod+${key}`;
}

/**
 * Page-level shortcuts that work wherever focus is, unlike ImageCanvas's own
 * bindings which need the canvas focused. Text fields keep their keys, and a
 * focused button keeps Enter/Space so keyboard activation still works.
 */
export function useHotkeys(hotkeys: readonly Hotkey[], enabled = true) {
  const latest = useRef(hotkeys);
  latest.current = hotkeys;

  useEffect(() => {
    if (!enabled) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.altKey || event.defaultPrevented) return;
      const key = eventKey(event);
      const hotkey = latest.current.find((candidate) => candidate.keys.includes(key));
      if (!hotkey) return;
      if (event.repeat && !hotkey.repeat) return;
      if (typingTarget(event.target) && !hotkey.allowInText) return;
      if ((key === "enter" || key === " ") && event.target instanceof HTMLButtonElement) return;
      event.preventDefault();
      hotkey.run(key);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [enabled]);
}

export const DIGIT_KEYS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"] as const;

export const SAVE_KEYS = ["mod+enter"] as const;

export const UNDO_KEYS = ["mod+z"] as const;

export const REDO_KEYS = ["mod+shift+z", "mod+y"] as const;

/** How to spell the `mod+` modifier for this platform in shortcut hints. */
export const MOD_LABEL =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent) ? "⌘" : "Ctrl";
