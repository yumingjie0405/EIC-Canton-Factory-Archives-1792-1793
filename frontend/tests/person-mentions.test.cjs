/**
 * Tests for the person-drawer mention wording (`src/lib/graphScope.ts`).
 *
 * The drawer used to print "该人物在本组没有直接提及记录" whenever the *linked* count was
 * zero, which reads as "this person never appears in the sources". Two very different
 * situations produce that zero and the UI must not conflate them:
 *
 *   1. the name is genuinely absent from the group;
 *   2. the name is present but identity linking has not confirmed the match yet.
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc src/lib/graphScope.ts \
 *        --outDir node_modules/.cache/eic-graphscope --module commonjs \
 *        --target es2020 --skipLibCheck
 *   node --test tests/person-mentions.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const scope = require(
  path.join(__dirname, "..", "node_modules", ".cache", "eic-graphscope", "graphScope.js")
);

const { personMentionNotice, personMentionHeadline } = scope;

test("linked mentions produce no warning", () => {
  const notice = personMentionNotice({ in_group_mention_count: 27, unlinked_name_match_count: 0 });
  assert.equal(notice.tone, "idle");
  assert.equal(notice.text, "");
});

test("same-name unlinked mentions are reported as a warning, not as absence", () => {
  const notice = personMentionNotice({ in_group_mention_count: 0, unlinked_name_match_count: 3 });
  assert.equal(notice.tone, "warn");
  assert.match(notice.text, /3 条同名提及/);
  assert.match(notice.text, /不是“档案里没有这个人”/);
});

test("a truly absent name is stated plainly but softly", () => {
  const notice = personMentionNotice({ in_group_mention_count: 0, unlinked_name_match_count: 0 });
  assert.equal(notice.tone, "idle");
  assert.match(notice.text, /确实没有同名提及记录/);
});

test("missing payload counts as unknown rather than as a negative finding", () => {
  const notice = personMentionNotice(null);
  assert.equal(notice.tone, "idle");
  assert.equal(personMentionHeadline(null), "组内提及 0 条");
});

test("headline separates linked from same-name unlinked evidence", () => {
  assert.equal(
    personMentionHeadline({ in_group_mention_count: 0, unlinked_name_match_count: 3 }),
    "组内提及 0 条 · 同名未链接 3 条"
  );
  assert.equal(
    personMentionHeadline({ in_group_mention_count: 12, unlinked_name_match_count: 0 }),
    "组内提及 12 条"
  );
  assert.equal(
    personMentionHeadline({ in_group_mention_count: 2, unlinked_name_match_count: 1 }),
    "组内提及 2 条 · 同名未链接 1 条"
  );
});

test("a linked count with leftover unlinked matches still shows both", () => {
  const notice = personMentionNotice({ in_group_mention_count: 1, unlinked_name_match_count: 4 });
  assert.equal(notice.tone, "idle", "linked mentions exist, so no warning is needed");
});

// ------------------------------------------------------- relationship panel facts

const { relationshipPanelFacts } = scope;

const REL = {
  id: 7,
  source_person_id: 1,
  target_person_id: 2,
  directionality: "directed",
  time_description: null,
};

test("panel states direction and the absence of a date explicitly", () => {
  const facts = relationshipPanelFacts(REL, [REL]);
  assert.match(facts.directionLabel, /有向/);
  assert.match(facts.timeLabel, /无日期信息/);
  assert.match(facts.timeLabel, /年份筛选/);
  assert.equal(facts.parallelLabel, null);
});

test("undirected claims say so instead of implying an arrow", () => {
  const facts = relationshipPanelFacts({ ...REL, directionality: "undirected" }, []);
  assert.match(facts.directionLabel, /无向/);
});

test("a date description is shown verbatim", () => {
  const facts = relationshipPanelFacts({ ...REL, time_description: "1792 年秋" }, []);
  assert.equal(facts.timeLabel, "1792 年秋");
});

test("parallel claims between the same pair are numbered", () => {
  const siblings = [
    REL,
    { ...REL, id: 9, directionality: "directed" },
    { ...REL, id: 11, directionality: "directed" },
  ];
  const second = relationshipPanelFacts(siblings[1], siblings);
  assert.match(second.parallelLabel, /共 3 条关系/);
  assert.match(second.parallelLabel, /第 2 条/);
  // A different pair must not be counted as parallel.
  const other = relationshipPanelFacts(
    { ...REL, id: 20, target_person_id: 99 },
    siblings
  );
  assert.equal(other.parallelLabel, null);
});

test("panel facts are safe for missing data", () => {
  const facts = relationshipPanelFacts(null, null);
  assert.equal(facts.directionLabel, "—");
  assert.equal(facts.timeLabel, "—");
  assert.equal(facts.parallelLabel, null);
});
