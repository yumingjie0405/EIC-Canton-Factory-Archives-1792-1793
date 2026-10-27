/**
 * Tests for the archival-context helpers and for the frontend source-tracking fix.
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc src/lib/blockContext.ts \
 *        --outDir node_modules/.cache/eic-blockcontext --module commonjs \
 *        --target es2020 --skipLibCheck
 *   node --test tests/block-context.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { execFileSync } = require("node:child_process");

const {
  checkEvidenceLocation,
  buildBlockContextQuery,
  blockTextSections,
  formatBlockLocation,
  neighbourSummary,
} = require(path.join(
  __dirname,
  "..",
  "node_modules",
  ".cache",
  "eic-blockcontext",
  "blockContext.js"
));

// --------------------------------------------------------------------------- //
// Locating the block: a card without a document id must not trigger a lookup
// --------------------------------------------------------------------------- //
test("a card with document and block ids can open the context", () => {
  assert.deepEqual(checkEvidenceLocation({ document_id: 7, block_id: 101 }), { ok: true });
});

test("a card without a block id is refused with an explicit reason", () => {
  const result = checkEvidenceLocation({ document_id: 7, block_id: 0 });
  assert.equal(result.ok, false);
  assert.match(result.reason, /没有关联的档案文本块/);
  assert.equal(checkEvidenceLocation({ document_id: 7, block_id: null }).ok, false);
  assert.equal(checkEvidenceLocation({ document_id: 7 }).ok, false);
});

test("a card without a document id is refused instead of guessing the document", () => {
  const result = checkEvidenceLocation({ document_id: null, block_id: 101 });
  assert.equal(result.ok, false);
  assert.match(result.reason, /缺少文档标识/);
  assert.equal(checkEvidenceLocation({ block_id: 101 }).ok, false);
});

// --------------------------------------------------------------------------- //
// Query building
// --------------------------------------------------------------------------- //
test("the query carries the document id and a clamped window", () => {
  assert.equal(buildBlockContextQuery(7, 1), "document_id=7&window=1");
  assert.equal(buildBlockContextQuery(7), "document_id=7&window=1");
  assert.equal(buildBlockContextQuery(7, 9), "document_id=7&window=3");
  assert.equal(buildBlockContextQuery(7, -2), "document_id=7&window=0");
});

// --------------------------------------------------------------------------- //
// Archival text sections: transcriptions only, empty ones skipped
// --------------------------------------------------------------------------- //
test("raw and corrected transcriptions are both exposed, in a fixed order", () => {
  const sections = blockTextSections({
    block_id: 1,
    page_id: 1,
    block_index: 0,
    raw_text: "raw transcription",
    corrected_text: "corrected transcription",
    has_text: true,
  });
  assert.deepEqual(
    sections.map((s) => [s.key, s.label, s.text]),
    [
      ["raw", "原始 OCR 转录", "raw transcription"],
      ["corrected", "校订 OCR 转录", "corrected transcription"],
    ]
  );
});

test("empty or whitespace-only texts are omitted, not padded", () => {
  const onlyRaw = blockTextSections({
    block_id: 1, page_id: 1, block_index: 0, raw_text: "text", corrected_text: "   ", has_text: true,
  });
  assert.deepEqual(onlyRaw.map((s) => s.key), ["raw"]);
  const none = blockTextSections({
    block_id: 1, page_id: 1, block_index: 0, raw_text: null, corrected_text: null, has_text: false,
  });
  assert.deepEqual(none, []);
  assert.deepEqual(blockTextSections(null), []);
});

test("model-derived fields are never turned into archival sections", () => {
  const sections = blockTextSections({
    block_id: 1,
    page_id: 1,
    block_index: 0,
    raw_text: "raw",
    corrected_text: null,
    has_text: true,
    // fields that must never be rendered as archival text
    modern_english: "MODEL ENGLISH",
    translation_zh: "模型译文",
  });
  assert.equal(sections.length, 1);
  assert.equal(sections[0].text, "raw");
  assert.doesNotMatch(JSON.stringify(sections), /MODEL ENGLISH|模型译文/);
});

// --------------------------------------------------------------------------- //
// Location labels only use stored values
// --------------------------------------------------------------------------- //
test("location label shows a page number only when one exists", () => {
  const block = { block_id: 101, page_id: 3, block_index: 2, block_label: "paragraph", has_text: true };
  assert.equal(
    formatBlockLocation(block, 12),
    "第 12 页 · Block #101 · 页内序号 2 · 类型 paragraph"
  );
  assert.equal(
    formatBlockLocation(block, null),
    "页码未记录 · Block #101 · 页内序号 2 · 类型 paragraph"
  );
  assert.equal(formatBlockLocation(null, 12), "定位信息不可用");
});

test("neighbour summary reports what the API returned", () => {
  assert.equal(
    neighbourSummary({ previous: [{}, {}], next: [{}] }),
    "同页前文 2 段 / 后文 1 段"
  );
  assert.equal(neighbourSummary({ previous: [{}, {}], next: [{}] }), "同页前文 2 段 / 后文 1 段");
  assert.equal(neighbourSummary(null), "");
});

// --------------------------------------------------------------------------- //
// The .gitignore fix: frontend source files are no longer ignored
// --------------------------------------------------------------------------- //
function isIgnored(relativePath) {
  try {
    execFileSync(process.env.GIT_BIN || "git", ["check-ignore", "-q", relativePath], {
      cwd: path.join(__dirname, "..", ".."),
      stdio: "ignore",
    });
    return true; // exit code 0 => ignored
  } catch (error) {
    if (error.status === 1) return false; // exit code 1 => not ignored
    if (error.code === "ENOENT") {
      throw new Error(
        "git executable not found on PATH; the source-tracking assertions need git " +
          "(add C:\\Program Files\\Git\\cmd to PATH or set GIT_BIN)"
      );
    }
    throw error;
  }
}

test("every frontend/src/lib source file is visible to git", () => {
  const libDir = path.join(__dirname, "..", "src", "lib");
  const files = fs.readdirSync(libDir).filter((name) => /\.(ts|tsx)$/.test(name));
  assert.ok(files.length >= 8, `expected the lib directory to hold sources, saw ${files.length}`);

  const ignored = files
    .map((name) => `frontend/src/lib/${name}`)
    .filter((relative) => isIgnored(relative));
  assert.deepEqual(ignored, [], "no frontend/src/lib source file may be git-ignored");
});

test("the python packaging rules still ignore root-level build output", () => {
  assert.equal(isIgnored("lib/some_package.py"), true);
  assert.equal(isIgnored("lib64/some_package.so"), true);
  assert.equal(isIgnored("dist/archive.tar.gz"), true);
  assert.equal(isIgnored("build/obj.o"), true);
});
