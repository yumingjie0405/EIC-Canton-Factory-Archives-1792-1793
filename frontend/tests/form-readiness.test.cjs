/**
 * Tests for the submit-readiness rules (`src/lib/formReadiness.ts`).
 *
 * Regression guard for the R2.6 browser defect where clicking "创建文档组"
 * produced no request and no message at all (the button was disabled because
 * React had not received the typed value yet), so any wait for the new list
 * item timed out with no diagnostic.
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc src/lib/formReadiness.ts \
 *        --outDir node_modules/.cache/eic-formreadiness --module commonjs \
 *        --target es2020 --skipLibCheck
 *   node --test tests/form-readiness.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  EMPTY_GROUP_NAME_MESSAGE,
  EMPTY_PLAN_QUESTION_MESSAGE,
  EMPTY_QUESTION_MESSAGE,
  BUSY_MESSAGE,
  isInteractive,
  blockedSubmitMessage,
} = require(path.join(
  __dirname,
  "..",
  "node_modules",
  ".cache",
  "eic-formreadiness",
  "formReadiness.js"
));

test("empty group name is reported instead of silently ignored", () => {
  assert.equal(
    blockedSubmitMessage({ value: "   ", emptyMessage: EMPTY_GROUP_NAME_MESSAGE }),
    "请输入文档组名称。"
  );
});

test("a filled group name submits", () => {
  assert.equal(
    blockedSubmitMessage({ value: "Canton 1792", emptyMessage: EMPTY_GROUP_NAME_MESSAGE }),
    null
  );
});

test("an empty research question tells the user what is missing", () => {
  assert.equal(
    blockedSubmitMessage({ value: "", emptyMessage: EMPTY_QUESTION_MESSAGE }),
    EMPTY_QUESTION_MESSAGE
  );
  assert.equal(
    blockedSubmitMessage({ value: "\n\t ", emptyMessage: EMPTY_PLAN_QUESTION_MESSAGE }),
    EMPTY_PLAN_QUESTION_MESSAGE
  );
});

test("busy requests never fire again and say why", () => {
  assert.equal(
    blockedSubmitMessage({ value: "question", emptyMessage: EMPTY_QUESTION_MESSAGE, busy: true }),
    BUSY_MESSAGE
  );
});

test("busy wins over empty: no duplicate message about a missing value", () => {
  assert.equal(
    blockedSubmitMessage({ value: "", emptyMessage: EMPTY_QUESTION_MESSAGE, busy: true }),
    BUSY_MESSAGE
  );
});

test("before hydration the form is not interactive and stays quiet", () => {
  assert.equal(isInteractive(false, false), false);
  assert.equal(isInteractive(true, false), true);
  assert.equal(isInteractive(true, true), false);
  assert.equal(
    blockedSubmitMessage({ value: "Canton", emptyMessage: EMPTY_GROUP_NAME_MESSAGE, hydrated: false }),
    null
  );
});

test("surrounding whitespace is not treated as content", () => {
  assert.equal(
    blockedSubmitMessage({ value: "   ", emptyMessage: EMPTY_GROUP_NAME_MESSAGE }),
    EMPTY_GROUP_NAME_MESSAGE
  );
  assert.equal(
    blockedSubmitMessage({ value: " 茶 ", emptyMessage: EMPTY_GROUP_NAME_MESSAGE }),
    null
  );
});
