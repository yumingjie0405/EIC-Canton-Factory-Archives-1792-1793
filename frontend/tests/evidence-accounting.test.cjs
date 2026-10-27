/**
 * Tests for the evidence-accounting helpers in `src/lib/graphScope.ts`.
 *
 * Guards the "14 620 条证据" misreading: row counts, distinct in-group blocks and
 * distinct quotes are three different quantities, and Mock-derived evidence must be
 * labelled as such instead of inflating an apparent finding.
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc src/lib/graphScope.ts \
 *        --outDir node_modules/.cache/eic-graphscope --module commonjs \
 *        --target es2020 --skipLibCheck
 *   node --test tests/evidence-accounting.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const scope = require(
  path.join(__dirname, "..", "node_modules", ".cache", "eic-graphscope", "graphScope.js")
);

const { relationshipEvidenceLabel, evidenceTrustLine, metricsNotice } = scope;

test("evidence label names the unit instead of showing a bare row count", () => {
  const label = relationshipEvidenceLabel({
    evidence_count: 3,
    evidence_rows: 6200,
    distinct_quotes: 1,
  });
  assert.match(label, /3 个区块/);
  assert.match(label, /6200 条证据行/);
  assert.match(label, /1 句原文/);
});

test("evidence label does not repeat a row count that equals the block count", () => {
  const label = relationshipEvidenceLabel({ evidence_count: 4, evidence_rows: 4 });
  assert.equal(label, "4 个区块");
});

test("evidence label is explicit when a claim has no in-group evidence", () => {
  assert.equal(relationshipEvidenceLabel({ evidence_count: 0, evidence_rows: 0 }), "暂无组内证据");
  assert.equal(relationshipEvidenceLabel(null), "证据未知");
});

test("a single block of evidence is not inflated by a missing row count", () => {
  assert.equal(relationshipEvidenceLabel({ evidence_count: 2 }), "2 个区块");
});

test("evidence trust line separates verified, mock and unknown rows", () => {
  assert.equal(
    evidenceTrustLine({
      verified_evidence_count: 2,
      synthetic_evidence_count: 14_600,
      unknown_evidence_count: 5,
    }),
    "2 条已验证来源 · 14600 条 Mock/桩来源 · 5 条来源不明"
  );
  assert.equal(evidenceTrustLine({ verified_evidence_count: 0 }), null);
  assert.equal(evidenceTrustLine(null), null);
});

test("metrics notice warns when centrality mixes in unverified edges", () => {
  const notice = metricsNotice({
    synthetic_edge_count: 38,
    unknown_edge_count: 1,
    verified_edge_count: 0,
    metrics_basis: "irrelevant when unverified edges exist",
  });
  assert.match(notice, /38 条 Mock\/桩关系/);
  assert.match(notice, /1 条来源不明关系/);
  assert.match(notice, /不能作为历史影响力结论/);
});

test("metrics notice falls back to the stated basis for fully verified networks", () => {
  const notice = metricsNotice({
    synthetic_edge_count: 0,
    unknown_edge_count: 0,
    verified_edge_count: 12,
    metrics_basis: "边权重来自当前范围内的全部关系证据。",
  });
  assert.equal(notice, "边权重来自当前范围内的全部关系证据。");
  assert.equal(metricsNotice(null), null);
});
