/**
 * Pure helpers for span annotation over a text document.
 *
 * The backend counts positions in Unicode code points (Python string
 * indices); the DOM and JavaScript strings count UTF-16 code units. Every
 * span here is in code points; `CodePointIndex` converts at the DOM edge.
 */

export type Span = Readonly<{ start: number; end: number; label: string }>;

/** A run of text covered by the same set of spans (indices into `spans`). */
export type Segment = Readonly<{
  start: number;
  end: number;
  /** UTF-16 offset of `start`, for mapping DOM selections back. */
  utf16Start: number;
  text: string;
  covering: readonly number[];
}>;

/** Code point ↔ UTF-16 offset conversion for one document. */
export class CodePointIndex {
  /** utf16[i] is the UTF-16 offset of code point i; utf16[length] is text.length. */
  private readonly utf16: Uint32Array;
  readonly length: number;

  constructor(readonly text: string) {
    const offsets: number[] = [];
    for (let unit = 0; unit < text.length; ) {
      offsets.push(unit);
      const code = text.codePointAt(unit) ?? 0;
      unit += code > 0xffff ? 2 : 1;
    }
    offsets.push(text.length);
    this.utf16 = Uint32Array.from(offsets);
    this.length = offsets.length - 1;
  }

  toUtf16(codePoint: number): number {
    return this.utf16[Math.max(0, Math.min(this.length, codePoint))];
  }

  /** The code point at or before a UTF-16 offset (a low surrogate rounds down). */
  toCodePoint(utf16Offset: number): number {
    let low = 0;
    let high = this.length;
    while (low < high) {
      const middle = (low + high + 1) >> 1;
      if (this.utf16[middle] <= utf16Offset) low = middle;
      else high = middle - 1;
    }
    return low;
  }

  slice(start: number, end: number): string {
    return this.text.slice(this.toUtf16(start), this.toUtf16(end));
  }
}

function compareSpans(labels: readonly string[]) {
  return (left: Span, right: Span) =>
    left.start - right.start ||
    left.end - right.end ||
    labels.indexOf(left.label) - labels.indexOf(right.label);
}

/** Spans in the order the backend stores them: start, end, then label order. */
export function sortSpans(spans: readonly Span[], labels: readonly string[]): Span[] {
  return [...spans].sort(compareSpans(labels));
}

function sameSpan(left: Span, right: Span): boolean {
  return left.start === right.start && left.end === right.end && left.label === right.label;
}

/** Add a span (ignored when an identical one exists); returns the sorted list. */
export function addSpan(spans: readonly Span[], span: Span, labels: readonly string[]): Span[] {
  if (span.end <= span.start || spans.some((existing) => sameSpan(existing, span))) {
    return [...spans];
  }
  return sortSpans([...spans, span], labels);
}

export function removeSpan(spans: readonly Span[], index: number): Span[] {
  return spans.filter((_, position) => position !== index);
}

/** Change one span's label; a relabel that would duplicate another span is a no-op. */
export function relabelSpan(
  spans: readonly Span[],
  index: number,
  label: string,
  labels: readonly string[],
): Span[] {
  const target = spans[index];
  if (!target) return [...spans];
  const next = { ...target, label };
  if (spans.some((span, position) => position !== index && sameSpan(span, next))) return [...spans];
  return sortSpans(
    spans.map((span, position) => (position === index ? next : span)),
    labels,
  );
}

const WHITESPACE = /\s/u;

/**
 * The span a selection makes: UTF-16 offsets in, code points out, with
 * surrounding whitespace trimmed (a double-click or sloppy drag usually
 * catches a space). Null when nothing but whitespace was selected.
 */
export function selectionToRange(
  index: CodePointIndex,
  anchorUtf16: number,
  focusUtf16: number,
): { start: number; end: number } | null {
  let start = index.toCodePoint(Math.min(anchorUtf16, focusUtf16));
  // An end inside a surrogate pair still includes the whole character.
  const endUnit = Math.max(anchorUtf16, focusUtf16);
  let end = index.toCodePoint(endUnit);
  if (index.toUtf16(end) < endUnit) end += 1;
  while (start < end && WHITESPACE.test(index.slice(start, start + 1))) start += 1;
  while (end > start && WHITESPACE.test(index.slice(end - 1, end))) end -= 1;
  return end > start ? { start, end } : null;
}

/**
 * Split the document at every span boundary. Each segment lists the spans
 * covering it, so overlapping and nested spans render as stacked marks.
 */
export function segmentText(index: CodePointIndex, spans: readonly Span[]): Segment[] {
  const cuts = new Set<number>([0, index.length]);
  for (const span of spans) {
    cuts.add(Math.max(0, Math.min(index.length, span.start)));
    cuts.add(Math.max(0, Math.min(index.length, span.end)));
  }
  const points = [...cuts].sort((left, right) => left - right);
  const segments: Segment[] = [];
  for (let position = 0; position < points.length - 1; position += 1) {
    const start = points[position];
    const end = points[position + 1];
    if (end <= start) continue;
    const covering: number[] = [];
    spans.forEach((span, spanIndex) => {
      if (span.start <= start && span.end >= end) covering.push(spanIndex);
    });
    segments.push({
      start,
      end,
      utf16Start: index.toUtf16(start),
      text: index.slice(start, end),
      covering,
    });
  }
  return segments;
}

/** Of the spans covering a segment, the innermost (shortest) one. */
export function innermostSpan(spans: readonly Span[], covering: readonly number[]): number | null {
  let best: number | null = null;
  for (const spanIndex of covering) {
    const span = spans[spanIndex];
    if (best === null) {
      best = spanIndex;
      continue;
    }
    const current = spans[best];
    if (span.end - span.start < current.end - current.start) best = spanIndex;
  }
  return best;
}
