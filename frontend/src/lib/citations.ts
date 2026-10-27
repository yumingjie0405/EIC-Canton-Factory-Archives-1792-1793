/**
 * Citation parsing and navigation helpers (pure, no React, no DOM).
 *
 * Numbering contract
 * ------------------
 * The backend numbers evidence **by position** in the `sources` array it returns:
 * `context_builder.fuse_and_build_context` rewrites each evidence item's
 * `source_id` to `证据 #<i>` while building the unified list, so citation `N`
 * always refers to `sources[N - 1]`. Validation uses the same rule
 * (`answer_integrity.check_citation_integrity`: a marker is valid when
 * `1 <= N <= len(sources)`).
 *
 * Therefore this module resolves citations **positionally** and never by
 * comparing `source_id` strings: sorting or filtering the array would otherwise
 * silently point a citation at the wrong document.
 */

/** Mirrors the backend `CITATION_PATTERN` (`证据\s*#\s*(\d+)`), brackets optional. */
const CITATION_TOKEN = /(\[)?证据\s*#\s*(\d+)(\])?/g;

export type AnswerSegment =
  | { kind: "text"; text: string }
  | { kind: "citation"; text: string; index: number };

/**
 * Split an answer into plain-text and citation segments.
 *
 * The concatenation of every segment's `text` is always exactly the input
 * string, so Chinese characters, newlines and Markdown structure survive
 * untouched.
 */
export function parseAnswerSegments(text: string): AnswerSegment[] {
  const source = text ?? "";
  const segments: AnswerSegment[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;

  CITATION_TOKEN.lastIndex = 0;
  while ((match = CITATION_TOKEN.exec(source)) !== null) {
    if (match.index > cursor) {
      segments.push({ kind: "text", text: source.slice(cursor, match.index) });
    }
    segments.push({ kind: "citation", text: match[0], index: Number(match[2]) });
    cursor = match.index + match[0].length;
  }
  if (cursor < source.length) {
    segments.push({ kind: "text", text: source.slice(cursor) });
  }
  return segments;
}

/** Same validity rule as `check_citation_integrity` (1-based, inclusive). */
export function isCitationInRange(index: number, sourceCount: number): boolean {
  if (!Number.isInteger(index)) return false;
  if (!Number.isFinite(sourceCount) || sourceCount <= 0) return false;
  return index >= 1 && index <= sourceCount;
}

/**
 * Return the evidence a citation points at, or `null` when the number is out of
 * range. Never falls back to another source.
 */
export function resolveCitationSource<T>(index: number, sources: readonly T[]): T | null {
  const list = sources ?? [];
  if (!isCitationInRange(index, list.length)) return null;
  return list[index - 1];
}

/** Human-readable label of a citation number, used for tooltips and a11y text. */
export function citationLabel(index: number): string {
  return `证据 #${index}`;
}

/**
 * Stable DOM anchor for one evidence card.
 *
 * The scope namespaces ids per research result (current RAG answer, current
 * Agent run, one saved history record), so several results can be rendered on
 * the same page without colliding.
 */
export function evidenceAnchorId(scope: string, index: number): string {
  return `evidence-${normalizeScope(scope)}-${index}`;
}

/** Keep scopes to characters that are safe in an `id` and a selector. */
export function normalizeScope(scope: string): string {
  const cleaned = String(scope ?? "")
    .trim()
    .replace(/[^A-Za-z0-9_-]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "");
  return cleaned || "default";
}
