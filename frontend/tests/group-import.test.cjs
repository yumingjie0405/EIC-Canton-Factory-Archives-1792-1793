/**
 * Tests for the per-file import rules (`src/lib/groupImport.ts`).
 *
 * Regression guard for the reported "batch Word import fails" case: several
 * multi-hundred-MB .docx exports in one multipart body never reach the server, so
 * the importer uploads them one at a time and must report each outcome — and an
 * oversized file must be rejected with a readable reason instead of a silent
 * no-op.
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc src/lib/groupImport.ts \
 *        --outDir node_modules/.cache/eic-groupimport --module commonjs \
 *        --target es2020 --skipLibCheck
 *   node --test tests/group-import.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  MAX_IMPORT_FILE_BYTES,
  MAX_IMPORT_FILE_MB,
  isFileTooLarge,
  megabytes,
  oversizedFileMessage,
  groupImportErrorMessage,
  summarizeImportResults,
  classifyImportResponse,
} = require(path.join(
  __dirname,
  "..",
  "node_modules",
  ".cache",
  "eic-groupimport",
  "groupImport.js"
));

test("the client limit matches the documented 512 MB ceiling", () => {
  assert.equal(MAX_IMPORT_FILE_MB, 512);
  assert.equal(MAX_IMPORT_FILE_BYTES, 512 * 1024 * 1024);
});

test("a 339 MB Word export is accepted, a 600 MB one is not", () => {
  assert.equal(isFileTooLarge(339 * 1024 * 1024), false);
  assert.equal(isFileTooLarge(600 * 1024 * 1024), true);
  assert.equal(isFileTooLarge(MAX_IMPORT_FILE_BYTES), false);
  assert.equal(isFileTooLarge(MAX_IMPORT_FILE_BYTES + 1), true);
});

test("oversized files are reported with size and limit", () => {
  const message = oversizedFileMessage("Letter_Book_1781.docx", 600 * 1024 * 1024);
  assert.match(message, /Letter_Book_1781\.docx/);
  assert.match(message, /600 MB/);
  assert.match(message, /上限 512 MB/);
  assert.match(message, /未上传/);
});

test("megabytes keeps one decimal", () => {
  assert.equal(megabytes(339.1 * 1024 * 1024), 339.1);
  assert.equal(megabytes(1024 * 1024), 1);
});

test("a browser network failure names the real cause instead of '导入失败'", () => {
  const message = groupImportErrorMessage(new TypeError("Failed to fetch"));
  assert.match(message, /请求未能发送到服务端/);
  assert.match(message, /512 MB/);
});

test("server 413 detail is shown verbatim", () => {
  const error = Object.assign(new Error("文件 a.docx 为 600.0 MB，超过单文件上限 512 MB。"), {
    status: 413,
  });
  assert.match(groupImportErrorMessage(error), /超过单文件上限/);
});

test("other server messages are preserved", () => {
  const error = Object.assign(new Error("Unsupported file type 'notes.txt'"), { status: 400 });
  assert.equal(groupImportErrorMessage(error), "Unsupported file type 'notes.txt'");
  assert.equal(groupImportErrorMessage(null), "导入失败（未返回原因）。");
});

test("summary distinguishes full success from partial failure", () => {
  assert.equal(
    summarizeImportResults([
      { name: "a.docx", ok: true, message: "新增 1 篇" },
      { name: "b.docx", ok: true, message: "新增 1 篇" },
    ]),
    "已导入 2 个文件。"
  );
  const partial = summarizeImportResults([
    { name: "a.docx", ok: true, message: "新增 1 篇" },
    { name: "b.docx", ok: false, message: "超过上限" },
  ]);
  assert.match(partial, /1 个文件成功/);
  assert.match(partial, /1 个失败/);
  assert.equal(summarizeImportResults([]), "");
});

test("a duplicate import is reported as existing, not as a failure", () => {
  const outcome = classifyImportResponse({
    accepted_count: 0,
    duplicate_count: 1,
    failed_count: 0,
    items: [{ error: null }],
  });
  assert.equal(outcome.ok, true);
  assert.match(outcome.message, /已存在/);
  assert.match(outcome.message, /去重/);
});

test("an accepted file reports how many were added", () => {
  const outcome = classifyImportResponse({
    accepted_count: 1,
    duplicate_count: 0,
    failed_count: 0,
    items: [{ error: null }],
  });
  assert.equal(outcome.ok, true);
  assert.match(outcome.message, /新增 1 篇/);
});

test("a per-file failure carries the server reason", () => {
  const outcome = classifyImportResponse({
    accepted_count: 0,
    duplicate_count: 0,
    failed_count: 1,
    items: [{ error: "无法解析 Word(.docx) 文件：not a zip" }],
  });
  assert.equal(outcome.ok, false);
  assert.match(outcome.message, /无法解析 Word/);
});

test("no acceptance and no duplicate is still a failure", () => {
  const outcome = classifyImportResponse({
    accepted_count: 0,
    duplicate_count: 0,
    failed_count: 0,
    items: [],
  });
  assert.equal(outcome.ok, false);
  assert.match(outcome.message, /未导入任何内容/);
});
