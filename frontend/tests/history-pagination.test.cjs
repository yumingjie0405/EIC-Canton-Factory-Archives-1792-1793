/**
 * Tests for the research-history paging helpers (`src/lib/historyPagination.ts`).
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc src/lib/historyPagination.ts \
 *        --outDir node_modules/.cache/eic-historypaging --module commonjs \
 *        --target es2020 --skipLibCheck
 *   node --test tests/history-pagination.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  HISTORY_PAGE_SIZE,
  mergeHistoryItems,
  historyHasMore,
  historyProgressLabel,
  nextHistoryPage,
  resetHistoryPaging,
} = require(path.join(
  __dirname,
  "..",
  "node_modules",
  ".cache",
  "eic-historypaging",
  "historyPagination.js"
));

/** Build a newest-first page of synthetic records: ids from `start` downwards. */
function page(start, count) {
  return Array.from({ length: count }, (_, i) => ({ id: start - i, question: `q${start - i}` }));
}

test("the default page size is 20", () => {
  assert.equal(HISTORY_PAGE_SIZE, 20);
});

test("a second page is appended after the first, oldest last", () => {
  const first = page(45, 20); // 45..26
  const second = page(25, 20); // 25..6
  const merged = mergeHistoryItems(first, second);

  assert.equal(merged.length, 40);
  assert.equal(merged[0].id, 45);
  assert.equal(merged[39].id, 6);
  const ids = merged.map((r) => r.id);
  assert.deepEqual(ids, [...ids].sort((a, b) => b - a), "order must stay newest-first");
});

test("45 records load completely over three pages without duplicates or gaps", () => {
  let loaded = [];
  loaded = mergeHistoryItems(loaded, page(45, 20)); // 45..26
  assert.equal(historyHasMore(loaded.length, 45), true);
  loaded = mergeHistoryItems(loaded, page(25, 20)); // 25..6
  assert.equal(historyHasMore(loaded.length, 45), true);
  loaded = mergeHistoryItems(loaded, page(5, 5)); // 5..1
  assert.equal(historyHasMore(loaded.length, 45), false);

  const ids = loaded.map((r) => r.id);
  assert.equal(ids.length, 45);
  assert.equal(new Set(ids).size, 45);
  assert.deepEqual(ids, Array.from({ length: 45 }, (_, i) => 45 - i));
});

test("an exact multiple of the page size reports no extra page", () => {
  let loaded = mergeHistoryItems(page(40, 20), page(20, 20)); // 40..1
  assert.equal(loaded.length, 40);
  assert.equal(historyHasMore(loaded.length, 40), false);
});

test("a record duplicated across pages is kept once, in its first position", () => {
  const first = page(45, 20); // 45..26
  const secondWithOverlap = [26, 25, 24].map((id) => ({ id, question: `q${id}` }));
  const merged = mergeHistoryItems(first, secondWithOverlap);

  assert.equal(merged.length, 22);
  assert.equal(merged.filter((r) => r.id === 26).length, 1);
  assert.equal(merged[19].id, 26);
  assert.equal(merged[20].id, 25);
});

test("a new record inserted between page loads causes no duplicate and no false end", () => {
  const first = page(45, 20); // 45..26
  // A new run (#46) shifts the server window; page 2 now repeats id 26.
  const secondAfterInsert = page(26, 20); // 26..7
  const merged = mergeHistoryItems(first, secondAfterInsert);

  assert.equal(new Set(merged.map((r) => r.id)).size, merged.length);
  assert.equal(merged.length, 39);
  // 46 was never fetched (it arrived after page 1), so more pages remain.
  assert.equal(historyHasMore(merged.length, 46), true);
});

test("a failed load keeps the already loaded records and remains retryable", () => {
  const loaded = page(45, 20);
  const unchanged = mergeHistoryItems(loaded, []);

  assert.deepEqual(unchanged.map((r) => r.id), loaded.map((r) => r.id));
  assert.equal(historyHasMore(unchanged.length, 45), true, "retry must still be offered");
});

test("progress label always reports loaded and total", () => {
  assert.equal(historyProgressLabel(20, 45), "已加载 20 / 共 45 条");
  assert.equal(historyProgressLabel(45, 45), "已加载 45 / 共 45 条");
  assert.equal(historyProgressLabel(0, 0), "已加载 0 / 共 0 条");
  assert.equal(historyProgressLabel(Number.NaN, 3), "已加载 0 / 共 3 条");
});

test("hasMore never asks for a page when the total is unknown or already loaded", () => {
  assert.equal(historyHasMore(0, 0), false);
  assert.equal(historyHasMore(0, Number.NaN), false);
  assert.equal(historyHasMore(20, 20), false);
  assert.equal(historyHasMore(19, 20), true);
});

test("nextHistoryPage advances and never returns page 0", () => {
  assert.equal(nextHistoryPage(1), 2);
  assert.equal(nextHistoryPage(3), 4);
  assert.equal(nextHistoryPage(0), 1);
  assert.equal(nextHistoryPage(-2), 1);
});

test("reset clears paging so a new result or mode switch starts from page 1", () => {
  const reset = resetHistoryPaging();
  assert.equal(reset.page, 1);
  assert.deepEqual(reset.items, []);
  assert.equal(reset.total, 0);
});
