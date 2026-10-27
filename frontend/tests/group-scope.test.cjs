/**
 * Tests for the group-scope helpers (`src/lib/groupScope.ts`).
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc src/lib/groupScope.ts \
 *        --outDir node_modules/.cache/eic-groupscope --module commonjs \
 *        --target es2020 --skipLibCheck
 *   node --test tests/group-scope.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  importStatusLabel,
  importStatusTone,
  isGroupUsable,
  scopeLabel,
  emptyGroupWarning,
  importSummary,
  parseGroupIdParam,
  batchIsPending,
  batchProgressLabel,
  isMissingScope,
  missingScopeMessage,
} = require(path.join(
  __dirname,
  "..",
  "node_modules",
  ".cache",
  "eic-groupscope",
  "lib",
  "groupScope.js"
));

const group = (overrides = {}) => ({
  id: 3,
  name: "Canton 1792",
  document_count: 5,
  ready_count: 3,
  failed_count: 0,
  created_at: "2026-10-24T00:00:00",
  ...overrides,
});

test("import statuses have readable labels and tones", () => {
  assert.equal(importStatusLabel("ready"), "可检索");
  assert.equal(importStatusLabel("failed"), "处理失败");
  assert.equal(importStatusLabel(undefined), "未知状态");
  assert.equal(importStatusTone("ready"), "ok");
  assert.equal(importStatusTone("queued"), "warn");
  assert.equal(importStatusTone("failed"), "bad");
  assert.equal(importStatusTone("pending"), "idle");
});

test("a group is usable only when something is searchable", () => {
  assert.equal(isGroupUsable(group()), true);
  assert.equal(isGroupUsable(group({ ready_count: 0 })), false);
  assert.equal(isGroupUsable(null), false);
});

test("scope label reports the group and its ready/total counts", () => {
  assert.equal(scopeLabel(group()), "范围：Canton 1792（3/5 篇可检索）");
  assert.equal(scopeLabel(null), "范围：全库（未限定文档组）");
});

test("an empty group warns that there is no fallback to the whole archive", () => {
  const warning = emptyGroupWarning(group({ document_count: 0, ready_count: 0 }));
  assert.match(warning, /不会回退到全库/);

  const pending = emptyGroupWarning(group({ ready_count: 0 }));
  assert.match(pending, /尚无可检索/);

  assert.equal(emptyGroupWarning(group()), null);
  assert.equal(emptyGroupWarning(null), null);
});

test("import summary keeps partial failures visible", () => {
  const summary = importSummary({
    batch_id: "abcdef123456",
    accepted_count: 2,
    duplicate_count: 1,
    failed_count: 1,
  });
  assert.equal(summary.batchId, "abcdef123456");
  assert.match(summary.message, /新增 2 篇/);
  assert.match(summary.message, /重复跳过 1 篇/);
  assert.match(summary.message, /失败 1 篇/);

  const clean = importSummary({
    batch_id: "x",
    accepted_count: 3,
    duplicate_count: 0,
    failed_count: 0,
  });
  assert.equal(clean.message, "新增 3 篇");
});

test("group id parsing rejects junk instead of guessing", () => {
  assert.equal(parseGroupIdParam("7"), 7);
  assert.equal(parseGroupIdParam(""), null);
  assert.equal(parseGroupIdParam(null), null);
  assert.equal(parseGroupIdParam("abc"), null);
  assert.equal(parseGroupIdParam("0"), null);
  assert.equal(parseGroupIdParam("-2"), null);
  assert.equal(parseGroupIdParam("1.5"), null);
});

test("a batch is pending while any document is still queued or running", () => {
  assert.equal(batchIsPending({ queued: 1, ready: 2 }), true);
  assert.equal(batchIsPending({ processing: 1 }), true);
  assert.equal(batchIsPending({ ready: 2, failed: 1 }), false);
  assert.equal(batchIsPending({}), false);
});

test("batch progress label combines ready and failed counts", () => {
  assert.equal(batchProgressLabel(3, { ready: 2, failed: 1 }), "可检索 2 / 共 3 篇，失败 1 篇");
  assert.equal(batchProgressLabel(2, { ready: 2 }), "可检索 2 / 共 2 篇");
});

test("an unknown requested scope is detected, never silently widened", () => {
  assert.equal(isMissingScope(999, [1, 2], {}), true);
  assert.equal(isMissingScope(2, [1, 2], {}), false);
  assert.equal(isMissingScope(null, [1, 2], {}), false);
  assert.equal(isMissingScope(undefined, [1, 2], {}), false);
  // while the list is still loading, or it failed, "missing" is not known yet
  assert.equal(isMissingScope(999, [], { isLoading: true }), false);
  assert.equal(isMissingScope(999, [], { hasError: true }), false);
  assert.equal(isMissingScope(999, [], {}), true);
});

test("missing scope message names the id and refuses a silent fallback", () => {
  const message = missingScopeMessage(999);
  assert.match(message, /#999/);
  assert.match(message, /不存在|无权访问/);
  assert.match(message, /不会退回到全库/);
});
