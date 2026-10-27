/**
 * Node test runner for the citation helpers (`src/lib/citations.ts`).
 *
 * The project has no frontend unit-test framework and this round must not add a
 * large dependency, so the pure helpers are compiled with the already-installed
 * TypeScript compiler and exercised with Node's built-in `node:test`.
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc src/lib/citations.ts \
 *        --outDir node_modules/.cache/eic-citations --module commonjs \
 *        --target es2020 --skipLibCheck
 *   node --test tests/citations.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const {
  parseAnswerSegments,
  isCitationInRange,
  resolveCitationSource,
  citationLabel,
  evidenceAnchorId,
  normalizeScope,
} = require(path.join(
  __dirname,
  "..",
  "node_modules",
  ".cache",
  "eic-citations",
  "citations.js"
));

const SRC = path.join(__dirname, "..", "src");

// --------------------------------------------------------------------------- //
// 1. A valid citation resolves to the matching source
// --------------------------------------------------------------------------- //
test("valid [证据 #1] resolves to the first source", () => {
  const sources = [{ source_id: "证据 #1" }, { source_id: "证据 #2" }];
  const segments = parseAnswerSegments("布朗向浩官支付了货款[证据 #1]。");

  const citations = segments.filter((s) => s.kind === "citation");
  assert.equal(citations.length, 1);
  assert.equal(citations[0].index, 1);
  assert.equal(citations[0].text, "[证据 #1]");
  assert.equal(isCitationInRange(1, sources.length), true);
  assert.equal(resolveCitationSource(1, sources), sources[0]);
});

// --------------------------------------------------------------------------- //
// 2. A non-existent number never points at another source
// --------------------------------------------------------------------------- //
test("out-of-range citation resolves to null", () => {
  const sources = [{ source_id: "证据 #1" }, { source_id: "证据 #2" }];
  assert.equal(isCitationInRange(9, sources.length), false);
  assert.equal(resolveCitationSource(9, sources), null);
  assert.equal(isCitationInRange(0, sources.length), false);
  assert.equal(resolveCitationSource(0, sources), null);
  assert.equal(isCitationInRange(-1, sources.length), false);
  assert.equal(isCitationInRange(1.5, sources.length), false);
  assert.equal(resolveCitationSource(1, []), null, "no sources => nothing to point at");
});

// --------------------------------------------------------------------------- //
// 3. Numbering follows array position, never source_id
// --------------------------------------------------------------------------- //
test("citation numbers follow array position, not source_id strings", () => {
  const reordered = [{ source_id: "证据 #2" }, { source_id: "证据 #1" }];
  // Position 2 is the second element even though its stored id says "#1":
  // the backend numbers evidence by position, so we must too.
  assert.equal(resolveCitationSource(2, reordered), reordered[1]);
  assert.equal(resolveCitationSource(1, reordered), reordered[0]);

  // A filtered/sorted array changes the mapping - which is exactly why the
  // renderer must be given the same array the answer was generated from.
  const filtered = reordered.slice(1);
  assert.equal(resolveCitationSource(2, filtered), null);
});

// --------------------------------------------------------------------------- //
// 4. Anchor ids are scoped per research record
// --------------------------------------------------------------------------- //
test("anchor ids are isolated per scope", () => {
  assert.notEqual(evidenceAnchorId("history-1", 1), evidenceAnchorId("history-2", 1));
  assert.equal(evidenceAnchorId("history-1", 1), evidenceAnchorId("history-1", 1));
  assert.equal(evidenceAnchorId("agent-abc", 3), "evidence-agent-abc-3");
  assert.equal(evidenceAnchorId("history-7", 2), "evidence-history-7-2");
});

test("scope normalisation keeps ids selector-safe", () => {
  assert.equal(normalizeScope("agent-1a2b3c"), "agent-1a2b3c");
  assert.equal(normalizeScope("history 9"), "history-9");
  assert.equal(normalizeScope("rag #1 / 证据"), "rag-1");
  assert.equal(normalizeScope(""), "default");
  assert.match(evidenceAnchorId("rag #1", 1), /^evidence-[A-Za-z0-9_-]+-1$/);
});

// --------------------------------------------------------------------------- //
// 5. Same parser for RAG, Agent and history text
// --------------------------------------------------------------------------- //
test("RAG-, Agent- and history-style answers parse identically", () => {
  const ragStyle = "根据商馆会议记录，双方议定毛织品合同[证据 #1]。";
  const agentStyle = "### 档案直接记载\n- 记录[证据 #1]。";
  const historyStyle = "- 记录[证据 #1]。";

  for (const text of [ragStyle, agentStyle, historyStyle]) {
    const citations = parseAnswerSegments(text).filter((s) => s.kind === "citation");
    assert.equal(citations.length, 1, text);
    assert.equal(citations[0].index, 1, text);
  }
});

test("no component re-implements its own citation parser", () => {
  const offenders = [];
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
        continue;
      }
      if (!entry.name.endsWith(".tsx") && !entry.name.endsWith(".ts")) continue;
      const text = fs.readFileSync(full, "utf8");
      // The old implementation split on this literal; a second implementation
      // would reintroduce a divergent numbering rule.
      if (text.includes('split(/(\\[证据') || text.includes('replace(/\\s+/g, "_")')) {
        offenders.push(path.relative(SRC, full));
      }
    }
  };
  walk(SRC);
  assert.deepEqual(offenders, [], "citation parsing must live only in src/lib/citations.ts");
});

// --------------------------------------------------------------------------- //
// 7. Chinese text, newlines and Markdown-ish structure survive parsing
// --------------------------------------------------------------------------- //
test("parsing preserves the original text exactly", () => {
  const text =
    "第一段：商馆会议记录记载了毛织品合同的议定过程[证据 #1]。\n" +
    "第二段：另见同一日咨询录[证据 #2]与后续记录。\n\n" +
    "- 列表项一\n- 列表项二（含中文标点，、；：）\n";
  const segments = parseAnswerSegments(text);
  assert.equal(segments.map((s) => s.text).join(""), text);
  assert.equal(segments.filter((s) => s.kind === "citation").length, 2);
  assert.ok(segments.some((s) => s.kind === "text" && s.text.includes("\n- 列表项一\n")));
});

test("unbracketed and oddly spaced markers are recognised like the backend does", () => {
  for (const text of ["证据 #2", "[证据#2]", "[证据  #  2]", "参见证据 #2。"]) {
    const citations = parseAnswerSegments(text).filter((s) => s.kind === "citation");
    assert.equal(citations.length, 1, text);
    assert.equal(citations[0].index, 2, text);
  }
});

test("text without citations yields a single text segment", () => {
  const segments = parseAnswerSegments("没有任何引用的普通句子。");
  assert.equal(segments.length, 1);
  assert.equal(segments[0].kind, "text");
  assert.equal(parseAnswerSegments("").length, 0);
});

test("citation labels match the backend marker format", () => {
  assert.equal(citationLabel(1), "证据 #1");
  assert.equal(citationLabel(12), "证据 #12");
});
