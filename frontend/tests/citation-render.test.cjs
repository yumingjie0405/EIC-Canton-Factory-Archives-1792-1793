/**
 * Server-render tests for the citation rendering component.
 *
 * Compiles the real `CitationRichText` component (and the shared helpers) to
 * CommonJS with the project's TypeScript compiler, then renders with
 * `react-dom/server` — already a dependency, so nothing new is installed.
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc -p tests/tsconfig.render.json
 *   node --test tests/citation-render.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");

const RENDER_DIR = path.join(__dirname, "..", "node_modules", ".cache", "eic-render");

// TypeScript leaves the `@/` path alias untouched (that is a bundler job), so
// resolve it here for the compiled test build only.
const Module = require("node:module");
const resolveFilename = Module._resolveFilename;
Module._resolveFilename = function (request, ...rest) {
  if (typeof request === "string" && request.startsWith("@/")) {
    request = path.join(RENDER_DIR, "src", request.slice(2));
  }
  return resolveFilename.call(this, request, ...rest);
};

const { CitationRichText } = require(path.join(
  RENDER_DIR,
  "src",
  "components",
  "rag",
  "CitationRichText.js"
));
const { evidenceAnchorId } = require(path.join(RENDER_DIR, "src", "lib", "citations.js"));

const h = React.createElement;

function render(text, sourceCount, onActivate) {
  return renderToStaticMarkup(h(CitationRichText, { text, sourceCount, onCitationActivate: onActivate }));
}

test("a valid citation renders as an actionable button carrying its number", () => {
  const html = render("货款已支付[证据 #1]。", 2);
  assert.match(html, /<button[^>]*data-citation-index="1"[^>]*data-citation-valid="true"/);
  assert.match(html, /\[证据 #1\]/);
  assert.match(html, /aria-label="跳转到证据 #1的档案证据卡片"/);
});

test("an out-of-range citation stays visible but is not actionable", () => {
  const html = render("凭空引注[证据 #9]。", 2);
  assert.doesNotMatch(html, /<button/, "invalid citations must not be buttons");
  assert.match(html, /data-citation-valid="false"/);
  assert.match(html, /data-citation-index="9"/);
  assert.match(html, /\[证据 #9\]/, "the original marker stays visible");
  assert.match(html, /本次仅有 2 条证据/);
});

test("a citation is invalid when no evidence exists at all", () => {
  const html = render("结论[证据 #1]。", 0);
  assert.doesNotMatch(html, /<button/);
  assert.match(html, /data-citation-valid="false"/);
});

test("only in-range numbers become buttons, mixed answers stay consistent", () => {
  const html = render("甲[证据 #1]，乙[证据 #2]，丙[证据 #7]。", 2);
  const buttons = html.match(/<button/g) || [];
  assert.equal(buttons.length, 2, "exactly the two valid citations are buttons");
  assert.match(html, /data-citation-index="7"[^>]*data-citation-valid="false"/);
});

test("Chinese text, newlines and list markers are preserved verbatim", () => {
  const text = "第一段：毛织品合同[证据 #1]。\n\n- 列表项：茶叶、生丝、白银\n- 第二项\n";
  const html = render(text, 1);
  const body = html.replace(/<[^>]+>/g, "");
  assert.equal(body, text, "text outside the citation token must be unchanged");
  assert.match(html, /- 列表项：茶叶、生丝、白银/);
});

test("the anchor id used by navigation matches the citation scope", () => {
  // Navigation looks up `evidenceAnchorId(scope, index)`; scope normalisation
  // must therefore be identical on both sides.
  assert.equal(evidenceAnchorId("history-3", 2), "evidence-history-3-2");
  const html = render("[证据 #2]", 2);
  assert.match(html, /data-citation-index="2"/);
});

test("the source card list computes anchors with the same helper", () => {
  const source = fs.readFileSync(
    path.join(__dirname, "..", "src", "components", "rag", "SourceCardList.tsx"),
    "utf8"
  );
  assert.match(source, /evidenceAnchorId\(safeScope, citationIndex\)/);
  assert.match(source, /data-evidence-scope=\{safeScope\}/);
  // No ad-hoc id building from source_id strings (that was the old, colliding scheme).
  assert.doesNotMatch(source, /id=\{`source-\$\{/);
});
