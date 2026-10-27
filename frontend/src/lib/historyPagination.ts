/**
 * Pure paging helpers for the saved research history list.
 *
 * The backend returns ``{items, total, page, page_size}`` sorted newest-first.
 * The panel loads one page at a time and appends, so the merge step has to be
 * free of duplicates even when a new research run shifts the server-side window
 * between requests. Everything here is pure so it can be unit-tested without a
 * browser or a network.
 */

export const HISTORY_PAGE_SIZE = 20;

export interface HistoryPageLike<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/** Append a page, keeping the first occurrence of each record id (newest-first order). */
export function mergeHistoryItems<T extends { id: number }>(
  existing: T[],
  incoming: T[]
): T[] {
  const seen = new Set<number>();
  const merged: T[] = [];
  for (const item of [...existing, ...incoming]) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    merged.push(item);
  }
  return merged;
}

/** True when the server reports more records than the client already holds. */
export function historyHasMore(loadedCount: number, total: number): boolean {
  if (!Number.isFinite(total) || total < 0) return false;
  return loadedCount < total;
}

/** Progress label such as `已加载 20 / 共 45 条`. */
export function historyProgressLabel(loadedCount: number, total: number): string {
  const loaded = Number.isFinite(loadedCount) && loadedCount > 0 ? loadedCount : 0;
  const known = Number.isFinite(total) && total > 0 ? total : 0;
  return `已加载 ${loaded} / 共 ${known} 条`;
}

/** Next page number, never below 1. */
export function nextHistoryPage(page: number): number {
  const current = Number.isFinite(page) && page > 0 ? Math.floor(page) : 0;
  return current + 1;
}

/** Reset state used when the mode filter changes or a new result arrives. */
export function resetHistoryPaging(): { page: number; items: []; total: number } {
  return { page: 1, items: [], total: 0 };
}
