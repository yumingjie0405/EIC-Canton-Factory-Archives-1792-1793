/**
 * Tests for the group-graph presentation rules (`src/lib/graphScope.ts`).
 *
 * These guard the two failure modes reported in review:
 *   1. an empty / not-extracted group must never be rendered as the global graph;
 *   2. labels must not promise progress or capabilities the backend cannot prove.
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc src/lib/graphScope.ts \
 *        --outDir node_modules/.cache/eic-graphscope --module commonjs \
 *        --target es2020 --skipLibCheck
 *   node --test tests/graph-scope.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const {
  SCOPE_STATE_LABEL,
  scopeStateTone,
  canRenderGraph,
  canStartExtraction,
  isRunActive,
  isRunPaused,
  resumeNeedsBudget,
  runStatusLabel,
  runProgressLabel,
  runBudgetLabel,
  autoResearchHeadline,
  autoResearchTone,
  autoOutcomeLabel,
  dispatchWarning,
  relationTypeLabel,
  buildRelationTypeOptions,
  yearFilterState,
  trustNotice,
  runRoleLines,
  budgetSourceLabel,
  cleanupCountLabel,
  extractionPanelDefaultOpen,
  relationshipProtectionLabel,
  coverageLabel,
  scopeBanner,
  viewLabel,
  excludedHint,
  groupEvidenceByDocument,
  evidenceLocator,
  isScopeMismatch,
} = require(path.join(
  __dirname,
  "..",
  "node_modules",
  ".cache",
  "eic-graphscope",
  "graphScope.js"
));

const scope = (overrides = {}) => ({
  group_id: 7,
  group_name: "东印度公司",
  document_count: 4,
  block_count: 11219,
  blocks_with_mentions: 350,
  mention_count: 900,
  pending_mention_reviews: 12,
  person_count: 25,
  relationship_count_official: 18,
  relationship_count_candidates: 3,
  extraction_state: "partial_extraction",
  state_message: "只有部分文本区块完成抽取",
  task_tracking_available: false,
  ...overrides,
});

test("every derived state has a label and a tone", () => {
  for (const state of [
    "no_documents",
    "no_text",
    "not_extracted",
    "queued_extraction",
    "extracting",
    "paused_extraction",
    "extraction_failed",
    "partial_extraction",
    "extracted_empty",
    "ready",
  ]) {
    assert.ok(SCOPE_STATE_LABEL[state], state);
    assert.ok(["ok", "warn", "bad", "idle"].includes(scopeStateTone(state)), state);
  }
  assert.equal(scopeStateTone("ready"), "ok");
  assert.equal(scopeStateTone("partial_extraction"), "warn");
  assert.equal(scopeStateTone("not_extracted"), "idle");
  assert.equal(scopeStateTone("extraction_failed"), "bad");
  assert.equal(scopeStateTone("paused_extraction"), "warn");
});

test("an empty or unextracted group never renders a graph", () => {
  assert.equal(canRenderGraph("no_documents"), false);
  assert.equal(canRenderGraph("no_text"), false);
  assert.equal(canRenderGraph("not_extracted"), false);
  assert.equal(canRenderGraph("extracted_empty"), false);
  assert.equal(canRenderGraph("extraction_failed"), false);
  assert.equal(canRenderGraph("ready"), true);
  assert.equal(canRenderGraph("partial_extraction"), true);
  // A run in flight can already have extracted evidence which must stay visible.
  assert.equal(canRenderGraph("extracting"), true);
  assert.equal(canRenderGraph("paused_extraction"), true);
});

test("extraction can start only when no run is working on the scope", () => {
  assert.equal(canStartExtraction("not_extracted"), true);
  assert.equal(canStartExtraction("ready"), true);
  assert.equal(canStartExtraction("extracted_empty"), true);
  assert.equal(canStartExtraction("paused_extraction"), true);
  assert.equal(canStartExtraction("extraction_failed"), true);
  assert.equal(canStartExtraction("queued_extraction"), false);
  assert.equal(canStartExtraction("extracting"), false);
  // Nothing to extract from: the button must not be offered.
  assert.equal(canStartExtraction("no_documents"), false);
  assert.equal(canStartExtraction("no_text"), false);
});

test("run state helpers only claim transitions the backend supports", () => {
  const run = (status) => ({ status, total_blocks: 5, attempted_blocks: 0 });
  assert.equal(isRunActive(run("queued")), true);
  assert.equal(isRunActive(run("running")), true);
  assert.equal(isRunActive(run("paused_budget")), false);
  assert.equal(isRunActive(null), false);
  assert.equal(isRunPaused(run("paused_manual")), true);
  assert.equal(isRunPaused(run("paused_error")), true);
  assert.equal(isRunPaused(run("running")), false);
  // A budget pause cannot resume without a new ceiling.
  assert.equal(resumeNeedsBudget(run("paused_budget")), true);
  assert.equal(resumeNeedsBudget(run("paused_manual")), false);
  assert.equal(resumeNeedsBudget(null), false);
});

test("run labels report real progress and never invent a percentage", () => {  assert.equal(runStatusLabel("paused_budget"), "因预算暂停");
  assert.equal(runStatusLabel("completed"), "已完成");
  assert.equal(runStatusLabel("something-else"), "something-else");
  const run = {
    status: "running",
    total_blocks: 20,
    processed_blocks: 12,
    skipped_blocks: 2,
    failed_blocks: 1,
    attempted_blocks: 15,
    progress_percent: 75,
    tokens_used: 4321,
    budget_blocks: 40,
    budget_tokens: null,
  };
  assert.equal(
    runProgressLabel(run),
    "已处理 15 / 20 个区块（75%） · 成功 12 · 跳过 2 · 失败 1 · tokens 4321"
  );
  assert.equal(runBudgetLabel(run), "区块上限 40");
  assert.equal(runBudgetLabel({ ...run, budget_blocks: null }), null);
});

test("coverage text reports real counts, including pending reviews", () => {
  assert.equal(
    coverageLabel(scope()),
    "文档 4 篇 · 文本区块 11219 · 已抽取区块 350 · 待审提及 12"
  );
  assert.equal(
    coverageLabel(scope({ pending_mention_reviews: 0 })),
    "文档 4 篇 · 文本区块 11219 · 已抽取区块 350"
  );
});

test("scope banner and view labels name the scope explicitly", () => {
  assert.equal(scopeBanner(scope()), "图谱范围：东印度公司");
  assert.equal(viewLabel("all"), "全部已抽取（含待审核）");
  assert.equal(viewLabel("official"), "正式图谱（已接受）");
  assert.equal(viewLabel("candidates"), "候选关系（待审核）");
});

test("exclusion hints explain why a view is empty without faking data", () => {
  assert.match(
    excludedHint(scope({ relationship_count_official: 0, relationship_count_candidates: 2 }), "official"),
    /候选关系/
  );
  assert.match(
    excludedHint(scope({ relationship_count_official: 0, relationship_count_candidates: 0 }), "official"),
    /没有符合纳入规则的关系/
  );
  assert.match(
    excludedHint(scope({ relationship_count_candidates: 0 }), "candidates"),
    /没有待审核/
  );
  assert.match(
    excludedHint(scope({ relationship_count_official: 0, relationship_count_candidates: 0 }), "all"),
    /还没有任何抽取出的关系/
  );
  assert.equal(excludedHint(scope({ relationship_count_official: 0, relationship_count_candidates: 4 }), "all"), null);
  assert.equal(excludedHint(scope(), "official"), null);
  assert.equal(excludedHint(scope({ relationship_count_candidates: 5 }), "candidates"), null);
});

test("evidence stays grouped per source document and never merged", () => {
  const rows = [
    { evidence_id: 1, document_id: 10, document_name: "A.docx", page_number: 1, block_id: 100, quote_text: "first", evidence_role: "supports" },
    { evidence_id: 2, document_id: 11, document_name: "B.docx", page_number: 2, block_id: 200, quote_text: "second", evidence_role: "supports" },
    { evidence_id: 3, document_id: 10, document_name: "A.docx", page_number: 3, block_id: 300, quote_text: "third", evidence_role: "contradicts" },
  ];
  const groups = groupEvidenceByDocument(rows);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0].rows.map((row) => row.evidence_id), [1, 3]);
  assert.deepEqual(groups[1].rows.map((row) => row.evidence_id), [2]);
});

test("evidence locator names document, page and block", () => {
  assert.equal(
    evidenceLocator({ document_id: 10, document_name: "A.docx", page_number: 3, block_id: 42 }),
    "A.docx · 第 3 页 · Block #42"
  );
});

test("a response from a different scope is flagged as a mismatch", () => {
  assert.equal(isScopeMismatch(7, 7), false);
  assert.equal(isScopeMismatch(7, 8), true);
  assert.equal(isScopeMismatch(null, 8), false);
  assert.equal(isScopeMismatch(7, undefined), false);
});

test("auto research headline names the real reason, never a fake success", () => {
  const on = {
    global_enabled: true,
    group_enabled: true,
    model_configured: true,
    budget_blocks: 400,
    effective: true,
    reason: "started",
    detail: "",
  };
  assert.match(autoResearchHeadline(on), /自动研究已启用/);
  assert.match(autoResearchHeadline(on), /400/);
  assert.equal(autoResearchTone(on), "ok");
  assert.equal(autoResearchTone(null), "idle");

  const globalOff = { ...on, effective: false, reason: "global_disabled" };
  assert.match(autoResearchHeadline(globalOff), /部署级开关关闭/);
  assert.equal(autoResearchTone(globalOff), "idle");

  const groupOff = { ...on, effective: false, reason: "group_disabled" };
  assert.match(autoResearchHeadline(groupOff), /本组已关闭/);

  const noModel = { ...on, effective: false, reason: "model_not_configured" };
  assert.match(autoResearchHeadline(noModel), /未配置模型/);
  assert.equal(autoResearchTone(noModel), "warn");

  assert.equal(autoResearchHeadline(null), "自动研究状态未知");
});

test("per-document auto outcomes are labelled without implying success", () => {
  assert.equal(autoOutcomeLabel("started"), "已自动排队");
  assert.equal(autoOutcomeLabel("already_active"), "已在队列中");
  assert.equal(autoOutcomeLabel("already_extracted"), "已抽取，跳过");
  assert.equal(autoOutcomeLabel("group_disabled"), "本组已关闭自动研究");
  assert.equal(autoOutcomeLabel("model_not_configured"), "未配置模型");
  assert.equal(autoOutcomeLabel("error"), "自动排队失败");
  assert.equal(autoOutcomeLabel("something-new"), "something-new");
});

test("a run that never reached the queue is surfaced, not hidden", () => {
  assert.equal(dispatchWarning(null), null);
  assert.equal(dispatchWarning({ dispatch_state: "dispatched" }), null);
  assert.match(dispatchWarning({ dispatch_state: "pending" }), /等待队列确认/);
  assert.match(
    dispatchWarning({ dispatch_state: "failed", dispatch_error: "broker unreachable" }),
    /broker unreachable/
  );
  assert.match(dispatchWarning({ dispatch_state: "failed" }), /队列投递失败/);
});

// ----------------------------------------------------------------- filters / facets

const facets = (overrides = {}) => ({
  relation_types: [
    { value: "commercial", count: 3 },
    { value: "other", count: 0 },
  ],
  dated_years: [{ year: 1792, count: 2 }],
  undated_relationship_count: 1,
  total_relationship_count: 3,
  time_filter_available: true,
  include_unknown_dates: true,
  mention_years: [{ year: 1792, count: 40 }],
  ...overrides,
});

test("relation type options come only from the data and never fail on unknown values", () => {
  const options = buildRelationTypeOptions(facets(), undefined);
  assert.deepEqual(
    options.map((option) => [option.value, option.count, option.disabled]),
    [
      ["commercial", 3, false],
      ["other", 0, true],
    ]
  );
  assert.equal(options[0].label, "商业贸易");
  assert.equal(relationTypeLabel("commercial"), "商业贸易");
  assert.match(relationTypeLabel("brand-new-type"), /未知类型（brand-new-type）/);

  // A selected type that has no matches stays visible but disabled: the empty result
  // must be explainable, not silently missing from the UI.
  const selected = buildRelationTypeOptions(
    facets({ relation_types: [{ value: "commercial", count: 3 }] }),
    ["guarantee"]
  );
  const guarantee = selected.find((option) => option.value === "guarantee");
  assert.equal(guarantee.count, 0);
  assert.equal(guarantee.disabled, true);
  assert.equal(guarantee.selected, true);

  assert.deepEqual(buildRelationTypeOptions(null, undefined), []);
  assert.deepEqual(buildRelationTypeOptions(null, ["debt"]).length, 1);
});

test("the year filter is disabled with a reason when no relationship has a date", () => {
  const mixed = yearFilterState(facets());
  assert.equal(mixed.available, true);
  assert.equal(mixed.years.length, 1);
  assert.match(mixed.undatedHint, /1 条关系没有日期/);
  assert.match(mixed.undatedHint, /当前仍会显示/);

  const undatedOnly = yearFilterState(
    facets({
      dated_years: [],
      time_filter_available: false,
      time_filter_reason: "本组关系暂无可用的日期信息，时间筛选不可用。",
      total_relationship_count: 2,
      undated_relationship_count: 2,
    })
  );
  assert.equal(undatedOnly.available, false);
  assert.match(undatedOnly.reason, /暂无可用的日期信息/);

  const empty = yearFilterState(
    facets({ dated_years: [], total_relationship_count: 0, undated_relationship_count: 0 })
  );
  assert.equal(empty.available, false);
  assert.match(empty.reason, /没有关系/);

  const excluded = yearFilterState(facets({ include_unknown_dates: false }));
  assert.match(excluded.undatedHint, /已被排除/);

  assert.equal(yearFilterState(null).available, false);
});

test("mock provenance is shown as a warning, never as data", () => {
  assert.equal(trustNotice(null), null);
  assert.equal(trustNotice({ mock_derived: false, mock_run_ids: [], note: "" }), null);
  assert.match(
    trustNotice({ mock_derived: true, mock_run_ids: [9], note: "Mock 运行 #9" }),
    /Mock 运行 #9/
  );
  assert.match(trustNotice({ mock_derived: true, mock_run_ids: [], note: "" }), /Mock/);
});

test("run history shows every role so a paused run cannot mask a finished one", () => {
  const finished = { run_id: 9, status: "completed" };
  const paused = { run_id: 10, status: "paused_manual" };
  const lines = runRoleLines({
    active_run: null,
    paused_run: paused,
    last_finished_run: finished,
    failed_run: null,
    recent_runs: [paused, finished],
  });
  assert.deepEqual(
    lines.map((line) => [line.role, line.run.run_id]),
    [
      ["paused", 10],
      ["finished", 9],
    ]
  );
  assert.deepEqual(runRoleLines(null), []);
});

test("budget provenance is labelled and a missing ceiling is not shown as zero", () => {
  assert.equal(budgetSourceLabel(null), "未知");
  assert.match(budgetSourceLabel({ budget_blocks: null }), /未设置上限/);
  assert.match(
    budgetSourceLabel({ budget_blocks: 200, budget_source: "default" }),
    /默认上限 200/
  );
  assert.match(
    budgetSourceLabel({ budget_blocks: 7, budget_source: "explicit" }),
    /人工指定 7/
  );
  assert.match(budgetSourceLabel({ budget_blocks: 6, budget_source: "auto" }), /自动研究上限 6/);
  assert.match(budgetSourceLabel({ budget_blocks: 5 }), /来源未知/);
});

test("cleanup preview labels separate deletable, ambiguous and protected rows", () => {
  assert.match(cleanupCountLabel("mentions_deletable"), /可清理/);
  assert.match(cleanupCountLabel("mentions_ambiguous"), /来源不确定/);
  assert.match(cleanupCountLabel("evidence_protected_referenced"), /直接引用/);
  assert.match(cleanupCountLabel("relationships_partially_cleaned"), /证据可清理/);
  // An unknown key is shown verbatim instead of being dropped.
  assert.equal(cleanupCountLabel("some_new_metric"), "some_new_metric");
});

test("relationship protection reasons are explained, not echoed as codes", () => {
  assert.match(relationshipProtectionLabel("has_unprovable_evidence"), /来源无法证明/);
  assert.match(relationshipProtectionLabel("accepted_claim"), /永不删除/);
  assert.equal(relationshipProtectionLabel("brand_new_reason"), "brand_new_reason");
});

test("the extraction panel only starts expanded when there is nothing to summarise", () => {
  // No run history: the panel holds the only way to start research.
  assert.equal(extractionPanelDefaultOpen({ extraction_state: "not_extracted" }), true);
  assert.equal(extractionPanelDefaultOpen({ extraction_state: "extracted_empty" }), true);
  // Run history exists: collapse it, the summary line is enough.
  assert.equal(
    extractionPanelDefaultOpen({
      extraction_state: "ready",
      extraction_runs: { recent_runs: [{ run_id: 9 }] },
    }),
    false
  );
  assert.equal(
    extractionPanelDefaultOpen({
      extraction_state: "paused_extraction",
      extraction_runs: { paused_run: { run_id: 16 }, recent_runs: [{ run_id: 16 }] },
    }),
    false
  );
  assert.equal(extractionPanelDefaultOpen({ extraction_state: "ready", extraction_runs: {} }), true);
  assert.equal(extractionPanelDefaultOpen(null), false);
});
