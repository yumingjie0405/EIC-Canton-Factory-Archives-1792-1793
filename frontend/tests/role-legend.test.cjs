/**
 * Tests for the role legend (`src/lib/graphScope.ts` helpers + category resolver).
 *
 * The legend used to advertise five identity classes while every node was painted with
 * the same grey, because person roles were never stored. Two rules keep that honest:
 *
 *   1. only categories that actually occur in the current view are listed, with counts;
 *   2. "no role recorded" is its own category (unknown), never folded into "other".
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc src/lib/graphScope.ts \
 *        --outDir node_modules/.cache/eic-graphscope --module commonjs \
 *        --target es2020 --skipLibCheck
 *   node --test tests/role-legend.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const scope = require(
  path.join(__dirname, "..", "node_modules", ".cache", "eic-graphscope", "graphScope.js")
);

const { roleLegendItems, roleCoverageLabel, ROLE_CATEGORY_ORDER } = scope;

test("only categories present in the view are listed", () => {
  const items = roleLegendItems({ hong_merchant: 3, unknown: 364 });
  assert.deepEqual(
    items.map((item) => item.key),
    ["hong_merchant", "unknown"]
  );
  assert.deepEqual(
    items.map((item) => item.count),
    [3, 364]
  );
});

test("a category with zero people never appears", () => {
  const items = roleLegendItems({ diplomatic: 0, official: 2 });
  assert.deepEqual(
    items.map((item) => item.key),
    ["official"]
  );
});

test("unknown is reported as its own category, not merged away", () => {
  const items = roleLegendItems({ unknown: 367 });
  assert.equal(items.length, 1);
  assert.equal(items[0].key, "unknown");
  assert.equal(items[0].count, 367);
});

test("legend order is stable regardless of count ordering", () => {
  const items = roleLegendItems({ unknown: 1, hong_merchant: 9, official: 5 });
  assert.deepEqual(
    items.map((item) => item.key),
    ROLE_CATEGORY_ORDER.filter((key) => ["hong_merchant", "official", "unknown"].includes(key))
  );
});

test("unknown keys from a future backend are ignored instead of inventing entries", () => {
  const items = roleLegendItems({ hong_merchant: 1, pirate_king: 7 });
  assert.deepEqual(
    items.map((item) => item.key),
    ["hong_merchant"]
  );
});

test("no counts means no entries (the caller falls back to the full palette)", () => {
  assert.deepEqual(roleLegendItems(null), []);
  assert.deepEqual(roleLegendItems(undefined), []);
});

test("coverage label states totals, known and unknown separately", () => {
  assert.equal(
    roleCoverageLabel(367, 0, 367),
    "人物 367 人 · 已知角色 0 人 · 角色未知 367 人"
  );
  assert.equal(
    roleCoverageLabel(10, 4, 6),
    "人物 10 人 · 已知角色 4 人 · 角色未知 6 人"
  );
  assert.equal(roleCoverageLabel(null, null, null), "人物 0 人 · 已知角色 0 人 · 角色未知 0 人");
});
