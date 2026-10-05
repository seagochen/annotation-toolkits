import { describe, expect, it } from "vitest";

import {
  CodePointIndex,
  addSpan,
  innermostSpan,
  relabelSpan,
  removeSpan,
  segmentText,
  selectionToRange,
  sortSpans,
  type Span,
} from "./text-span";

const LABELS = ["PER", "LOC"];
// "🎉" is one code point but two UTF-16 units.
const TEXT = "🎉 张三在北京\n\n李四";

describe("CodePointIndex", () => {
  it("converts between code points and UTF-16 offsets", () => {
    const index = new CodePointIndex(TEXT);
    expect(index.length).toBe([...TEXT].length);
    expect(TEXT.length).toBe(index.length + 1);
    expect(index.toUtf16(0)).toBe(0);
    expect(index.toUtf16(1)).toBe(2);
    expect(index.toUtf16(index.length)).toBe(TEXT.length);
    expect(index.toCodePoint(2)).toBe(1);
    // The low surrogate of the emoji rounds down to the emoji itself.
    expect(index.toCodePoint(1)).toBe(0);
    expect(index.toCodePoint(TEXT.length)).toBe(index.length);
    expect(index.slice(2, 4)).toBe("张三");
  });

  it("handles empty text", () => {
    const index = new CodePointIndex("");
    expect(index.length).toBe(0);
    expect(index.toCodePoint(0)).toBe(0);
    expect(segmentText(index, [])).toEqual([]);
  });
});

describe("selectionToRange", () => {
  const index = new CodePointIndex(TEXT);

  it("maps a selection in either direction and trims whitespace", () => {
    const start = TEXT.indexOf("张");
    const end = TEXT.indexOf("京") + 1;
    expect(selectionToRange(index, start, end)).toEqual({ start: 2, end: 7 });
    expect(selectionToRange(index, end, start)).toEqual({ start: 2, end: 7 });
    // Leading space and the trailing blank lines are dropped.
    expect(selectionToRange(index, 2, TEXT.indexOf("李"))).toEqual({ start: 2, end: 7 });
    expect(selectionToRange(index, TEXT.indexOf("\n"), TEXT.indexOf("李"))).toBeNull();
  });

  it("includes a whole astral character when the selection ends inside it", () => {
    expect(selectionToRange(index, 0, 1)).toEqual({ start: 0, end: 1 });
  });
});

describe("span list editing", () => {
  it("keeps spans sorted, ignores duplicates and relabels", () => {
    let spans: Span[] = [];
    spans = addSpan(spans, { start: 5, end: 7, label: "LOC" }, LABELS);
    spans = addSpan(spans, { start: 2, end: 4, label: "LOC" }, LABELS);
    spans = addSpan(spans, { start: 2, end: 4, label: "PER" }, LABELS);
    spans = addSpan(spans, { start: 2, end: 4, label: "PER" }, LABELS);
    spans = addSpan(spans, { start: 3, end: 3, label: "PER" }, LABELS);
    expect(spans).toEqual([
      { start: 2, end: 4, label: "PER" },
      { start: 2, end: 4, label: "LOC" },
      { start: 5, end: 7, label: "LOC" },
    ]);
    // Relabelling into an existing duplicate is refused.
    expect(relabelSpan(spans, 1, "PER", LABELS)).toEqual(spans);
    expect(relabelSpan(spans, 2, "PER", LABELS)[2]).toEqual({ start: 5, end: 7, label: "PER" });
    expect(removeSpan(spans, 0)).toEqual(spans.slice(1));
    expect(sortSpans([...spans].reverse(), LABELS)).toEqual(spans);
  });
});

describe("segmentText", () => {
  it("splits at every boundary so overlapping and nested spans stack", () => {
    const index = new CodePointIndex(TEXT);
    const spans: Span[] = [
      { start: 2, end: 6, label: "PER" },
      { start: 4, end: 10, label: "LOC" },
      { start: 4, end: 6, label: "PER" },
    ];
    const segments = segmentText(index, spans);
    expect(segments.map((segment) => segment.text).join("")).toBe(TEXT);
    expect(segments.map((segment) => [segment.text, segment.covering])).toEqual([
      ["🎉 ", []],
      ["张三", [0]],
      ["在北", [0, 1, 2]],
      ["京\n\n李", [1]],
      ["四", []],
    ]);
    expect(segments[1].utf16Start).toBe(3);
    expect(innermostSpan(spans, segments[2].covering)).toBe(2);
    expect(innermostSpan(spans, [])).toBeNull();
  });
});
