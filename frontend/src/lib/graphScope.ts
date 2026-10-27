/**
 * Pure presentation rules for the group-scoped person graph.
 *
 * The backend derives the graph from the group's own evidence and reports the state
 * it can actually prove. These helpers only decide how to *label* that state — they
 * never invent progress, percentages or buttons for capabilities that do not exist.
 */

// Structural mirrors of the API types, so this module compiles standalone for the
// node test runner without depending on the Next.js path alias.
export type GraphView = "all" | "official" | "candidates";

export type GroupGraphState =
  | "no_documents"
  | "no_text"
  | "not_extracted"
  | "queued_extraction"
  | "extracting"
  | "paused_extraction"
  | "extraction_failed"
  | "partial_extraction"
  | "extracted_empty"
  | "ready";

/** Status values the extraction ledger can actually report. */
export type ExtractionRunStatus =
  | "queued"
  | "running"
  | "paused_budget"
  | "paused_error"
  | "paused_format"
  | "paused_manual"
  | "completed"
  | "partial"
  | "failed";

export interface ExtractionRunLike {
  run_id: number;
  scope_type: string;
  scope_id: number;
  group_id?: number | null;
  document_id?: number | null;
  status: ExtractionRunStatus | string;
  model?: string | null;
  is_mock_model?: boolean;
  trigger?: string;
  dispatch_state?: string;
  dispatch_attempts?: number;
  dispatch_error?: string | null;
  budget_source?: string | null;
  reset_at?: string | null;
  reset_reason?: string | null;
  total_blocks: number;
  processed_blocks: number;
  skipped_blocks: number;
  failed_blocks: number;
  attempted_blocks: number;
  progress_percent: number;
  tokens_used: number;
  budget_blocks?: number | null;
  budget_tokens?: number | null;
  pause_reason?: string | null;
}

export interface GroupScopeLike {
  group_id: number;
  group_name: string;
  document_count: number;
  block_count: number;
  blocks_with_mentions: number;
  blocks_with_extraction?: number;
  mention_count: number;
  pending_mention_reviews: number;
  person_count: number;
  relationship_count_official: number;
  relationship_count_candidates: number;
  extraction_state: GroupGraphState;
  state_message: string;
  task_tracking_available: boolean;
  extraction_run?: ExtractionRunLike | null;
  /** Classified run history from the API (same shape the panel renders). */
  extraction_runs?: RunsSummaryLike | null;
  auto_research?: AutoResearchLike | null;
}

export const SCOPE_STATE_LABEL: Record<GroupGraphState, string> = {
  no_documents: "组内没有文档",
  no_text: "组内文档尚无文本",
  not_extracted: "尚未抽取人物图谱",
  queued_extraction: "抽取已排队",
  extracting: "正在抽取",
  paused_extraction: "抽取已暂停",
  extraction_failed: "抽取失败",
  partial_extraction: "部分抽取",
  extracted_empty: "已抽取但无有效人物",
  ready: "图谱可用",
};

export type ScopeTone = "ok" | "warn" | "bad" | "idle";

export function scopeStateTone(state: GroupGraphState): ScopeTone {
  switch (state) {
    case "ready":
      return "ok";
    case "extracting":
    case "queued_extraction":
      return "warn";
    case "paused_extraction":
    case "partial_extraction":
      return "warn";
    case "extraction_failed":
      return "bad";
    case "not_extracted":
    case "extracted_empty":
      return "idle";
    default:
      return "warn";
  }
}

/**
 * Whether the group can render a graph at all. An empty scope is *not* an error and
 * must not be replaced by the global graph. A run that is still working on the group
 * can already have extracted evidence, which is shown while it continues.
 */
export function canRenderGraph(state: GroupGraphState): boolean {
  switch (state) {
    case "ready":
    case "partial_extraction":
    case "extracting":
    case "queued_extraction":
    case "paused_extraction":
      return true;
    default:
      return false;
  }
}

/** Extraction may be started only when no run is currently working on the scope. */
export function canStartExtraction(state: GroupGraphState): boolean {
  return state !== "queued_extraction" && state !== "extracting" && state !== "no_documents"
    && state !== "no_text";
}

export function isRunActive(run: ExtractionRunLike | null | undefined): boolean {
  if (!run) return false;
  return run.status === "queued" || run.status === "running";
}

export function isRunPaused(run: ExtractionRunLike | null | undefined): boolean {
  if (!run) return false;
  return String(run.status).startsWith("paused_");
}

/**
 * A budget pause cannot be resumed without a new ceiling: the backend refuses it,
 * so the UI must ask for a new limit instead of pretending "resume" will work.
 */
export function resumeNeedsBudget(run: ExtractionRunLike | null | undefined): boolean {
  return !!run && run.status === "paused_budget";
}

export const RUN_STATUS_LABEL: Record<string, string> = {
  queued: "已排队",
  running: "运行中",
  paused_budget: "因预算暂停",
  paused_error: "因服务异常暂停",
  paused_format: "因输出格式暂停",
  paused_manual: "已人工暂停",
  completed: "已完成",
  partial: "部分完成（有失败区块）",
  failed: "失败",
};

export function runStatusLabel(status: string): string {
  return RUN_STATUS_LABEL[status] ?? status;
}

/** Real progress text: attempted/total and tokens actually spent. */
export function runProgressLabel(run: ExtractionRunLike): string {
  const parts = [
    `已处理 ${run.attempted_blocks} / ${run.total_blocks} 个区块（${run.progress_percent}%）`,
    `成功 ${run.processed_blocks}`,
  ];
  if (run.skipped_blocks > 0) parts.push(`跳过 ${run.skipped_blocks}`);
  if (run.failed_blocks > 0) parts.push(`失败 ${run.failed_blocks}`);
  parts.push(`tokens ${run.tokens_used}`);
  return parts.join(" · ");
}

/** Budget text for the run card; never invents a ceiling that was not configured. */
export function runBudgetLabel(run: ExtractionRunLike): string | null {
  const parts: string[] = [];
  if (run.budget_blocks != null) parts.push(`区块上限 ${run.budget_blocks}`);
  if (run.budget_tokens != null) parts.push(`token 上限 ${run.budget_tokens}`);
  return parts.length ? parts.join(" · ") : null;
}

export function coverageLabel(scope: GroupScopeLike): string {
  const parts = [
    `文档 ${scope.document_count} 篇`,
    `文本区块 ${scope.block_count}`,
    `已抽取区块 ${scope.blocks_with_extraction ?? scope.blocks_with_mentions}`,
  ];
  if (scope.pending_mention_reviews > 0) {
    parts.push(`待审提及 ${scope.pending_mention_reviews}`);
  }
  return parts.join(" · ");
}

export function scopeBanner(scope: GroupScopeLike): string {
  return `图谱范围：${scope.group_name}`;
}

export function viewLabel(view: GraphView): string {
  if (view === "all") return "全部已抽取（含待审核）";
  if (view === "candidates") return "候选关系（待审核）";
  return "正式图谱（已接受）";
}

export function excludedHint(scope: GroupScopeLike, view: GraphView): string | null {
  if (view === "candidates") {
    if (scope.relationship_count_candidates === 0) return "该文档组没有待审核的候选关系。";
    return null;
  }
  if (view === "all") {
    if (scope.relationship_count_official + scope.relationship_count_candidates === 0) {
      return "该文档组还没有任何抽取出的关系。";
    }
    return null;
  }
  if (scope.relationship_count_official === 0) {
    return scope.relationship_count_candidates > 0
      ? "该文档组没有已接受的关系；可切换到“候选关系”查看待审核记录。"
      : "该文档组没有符合纳入规则的关系。";
  }
  return null;
}

/** Evidence rows grouped by their source document (never merged into one quote). */
export interface EvidenceRow {
  evidence_id: number;
  document_id: number;
  document_name: string;
  page_number: number;
  block_id: number;
  quote_text?: string | null;
  block_text?: string | null;
  evidence_role: string;
}

export interface EvidenceGroup {
  document_id: number;
  document_name: string;
  rows: EvidenceRow[];
}

export function groupEvidenceByDocument(rows: EvidenceRow[]): EvidenceGroup[] {
  const groups = new Map<number, EvidenceGroup>();
  for (const row of rows) {
    const existing = groups.get(row.document_id);
    if (existing) {
      existing.rows.push(row);
    } else {
      groups.set(row.document_id, {
        document_id: row.document_id,
        document_name: row.document_name,
        rows: [row],
      });
    }
  }
  return Array.from(groups.values()).sort((a, b) => a.document_id - b.document_id);
}

export function evidenceLocator(row: EvidenceRow): string {
  return `${row.document_name} · 第 ${row.page_number} 页 · Block #${row.block_id}`;
}

/** True only when the server reports a different scope than the one on screen. */
export function isScopeMismatch(
  requestedGroupId: number | null,
  responseGroupId: number | undefined
): boolean {
  if (requestedGroupId == null || responseGroupId == null) return false;
  return requestedGroupId !== responseGroupId;
}

// ---------------------------------------------------------------- auto research

export interface AutoResearchLike {
  global_enabled: boolean;
  group_enabled: boolean;
  model_configured: boolean;
  budget_blocks: number;
  budget_tokens?: number | null;
  model?: string | null;
  effective: boolean;
  reason: string;
  detail: string;
}

/**
 * One line that never conflates the three different facts:
 * auto research is configured, a run is running, and a relationship was reviewed.
 */
export function autoResearchHeadline(status: AutoResearchLike | null | undefined): string {
  if (!status) return "自动研究状态未知";
  if (status.effective) {
    return status.budget_blocks > 0
      ? `自动研究已启用（单次任务上限 ${status.budget_blocks} 个区块）`
      : "自动研究已启用";
  }
  switch (status.reason) {
    case "global_disabled":
      return "自动研究未启用：部署级开关关闭";
    case "group_disabled":
      return "自动研究未启用：本组已关闭";
    case "model_not_configured":
      return "自动研究未启用：未配置模型连接";
    default:
      return "自动研究未启用";
  }
}

/** Tone for the banner: enabled / blocked by config / off. */
export function autoResearchTone(
  status: AutoResearchLike | null | undefined
): "ok" | "warn" | "idle" {
  if (!status) return "idle";
  if (status.effective) return "ok";
  if (status.reason === "model_not_configured") return "warn";
  return "idle";
}

/** Human label for a per-document auto-research outcome. */
export function autoOutcomeLabel(reason: string): string {
  switch (reason) {
    case "started":
      return "已自动排队";
    case "already_active":
      return "已在队列中";
    case "already_extracted":
      return "已抽取，跳过";
    case "no_text":
      return "无文本，跳过";
    case "group_disabled":
      return "本组已关闭自动研究";
    case "global_disabled":
      return "自动研究未启用";
    case "model_not_configured":
      return "未配置模型";
    case "conflict":
      return "已有任务，未重复创建";
    case "error":
      return "自动排队失败";
    default:
      return reason;
  }
}

/** A run whose message never reached the queue must be visible, not hidden. */
export function dispatchWarning(run: ExtractionRunLike | null | undefined): string | null {
  if (!run) return null;
  if (run.dispatch_state === "failed") {
    return run.dispatch_error
      ? `任务已登记但队列投递失败：${run.dispatch_error}。可重新投递。`
      : "任务已登记但队列投递失败，可重新投递。";
  }
  if (run.dispatch_state === "pending") {
    return "任务已登记，正在等待队列确认。";
  }
  return null;
}

// ------------------------------------------------------------------- facets / filters

export interface FacetCount {
  value: string;
  count: number;
}

export interface FacetYear {
  year: number;
  count: number;
}

export interface FacetsLike {
  relation_types: FacetCount[];
  dated_years: FacetYear[];
  year_range?: { min: number; max: number } | null;
  undated_relationship_count: number;
  total_relationship_count: number;
  time_filter_available: boolean;
  time_filter_reason?: string | null;
  include_unknown_dates: boolean;
  mention_years?: FacetYear[];
  mention_year_note?: string | null;
  scope_person_count?: number;
  data_trust?: {
    mock_derived: boolean;
    mock_run_ids: number[];
    models: string[];
    note: string;
  } | null;
}

export const RELATION_TYPE_LABELS: Record<string, string> = {
  commercial: "商业贸易",
  guarantee: "保商担保",
  debt: "借贷债务",
  diplomatic: "使节外交",
  correspondence: "书信来往",
  kinship: "亲族关系",
  employment: "雇佣主仆",
  translation: "通事通译",
  accompaniment: "随行同行",
  other: "其他交往",
};

/** Never fail on an unknown enum value: fall back to the raw value, clearly marked. */
export function relationTypeLabel(value: string): string {
  return RELATION_TYPE_LABELS[value] ?? `未知类型（${value}）`;
}

export interface RelationTypeOption {
  value: string;
  label: string;
  count: number;
  disabled: boolean;
  selected: boolean;
}

/**
 * Options for the relation-type filter, derived **only** from the group's own data.
 * Types that exist nowhere in the group are not offered as if they did; a selected
 * type that has zero matches stays visible (disabled) so the empty result is explainable.
 */
export function buildRelationTypeOptions(
  facets: FacetsLike | null | undefined,
  selected: string[] | undefined
): RelationTypeOption[] {
  const counts = new Map<string, number>();
  for (const item of facets?.relation_types ?? []) {
    counts.set(item.value, item.count);
  }
  for (const value of selected ?? []) {
    if (!counts.has(value)) counts.set(value, 0);
  }
  return Array.from(counts.entries())
    .map(([value, count]) => ({
      value,
      label: relationTypeLabel(value),
      count,
      disabled: count === 0,
      selected: (selected ?? []).includes(value),
    }))
    .sort((a, b) => b.count - a.count || a.value.localeCompare(b.value));
}

export interface YearFilterState {
  available: boolean;
  /** Years that actually carry relationships, with counts. */
  years: FacetYear[];
  reason: string | null;
  undated: number;
  total: number;
  /** True when undated relationships are currently included by the backend filter. */
  includesUndated: boolean;
  /** Explanation the UI must show next to the checkbox. */
  undatedHint: string | null;
}

/**
 * The year filter applies to *relationship* dates. When the group has no dated
 * relationship the control must be disabled with a reason — never silently show years
 * that come from elsewhere (e.g. mention dates or a hard-coded template).
 */
export function yearFilterState(facets: FacetsLike | null | undefined): YearFilterState {
  const total = facets?.total_relationship_count ?? 0;
  const undated = facets?.undated_relationship_count ?? 0;
  const years = facets?.dated_years ?? [];
  const available = Boolean(facets?.time_filter_available) && years.length > 0;
  let reason: string | null = null;
  if (!facets) {
    reason = "筛选项加载中…";
  } else if (total === 0) {
    reason = "当前视图下没有关系，年份筛选不可用。";
  } else if (!available) {
    reason = facets.time_filter_reason ?? "本组关系暂无可用的日期信息，时间筛选不可用。";
  }
  const includesUndated = Boolean(facets?.include_unknown_dates);
  const undatedHint =
    undated > 0
      ? `有 ${undated} 条关系没有日期${includesUndated ? "，当前仍会显示（包含未标日期的关系）" : "，已被排除"}。`
      : null;
  return { available, years, reason, undated, total, includesUndated, undatedHint };
}

/** Mock-derived data must be labelled, not presented as archival evidence. */
export function trustNotice(
  trust: { mock_derived: boolean; mock_run_ids: number[]; note: string } | null | undefined
): string | null {
  if (!trust || !trust.mock_derived) return null;
  return trust.note || "该图谱包含 Mock 模型生成的记录，仅用于流程验证，不是档案发现。";
}

// ------------------------------------------------------------- evidence accounting

export interface RelationshipEvidenceLike {
  evidence_count?: number | null;
  evidence_rows?: number | null;
  distinct_quotes?: number | null;
  verified_evidence_count?: number | null;
  synthetic_evidence_count?: number | null;
  unknown_evidence_count?: number | null;
}

/**
 * Human label for a relationship's supporting evidence.
 *
 * Three different numbers live here and the UI used to show only the biggest one:
 * `evidence_rows` is stored rows, `evidence_count` is distinct in-group blocks, and
 * `distinct_quotes` is how many different sentences were actually extracted. A claim
 * repeated verbatim in 6 000 blocks is one claim, so the label always names the unit.
 */
export function relationshipEvidenceLabel(
  relationship: RelationshipEvidenceLike | null | undefined
): string {
  if (!relationship) return "证据未知";
  const blocks = relationship.evidence_count ?? 0;
  const rows = relationship.evidence_rows ?? blocks;
  const quotes = relationship.distinct_quotes;
  if (blocks === 0 && rows === 0) return "暂无组内证据";
  const parts = [`${blocks} 个区块`];
  if (rows > blocks) parts.push(`${rows} 条证据行`);
  if (typeof quotes === "number" && quotes > 0) parts.push(`${quotes} 句原文`);
  return parts.join(" · ");
}

/** Provenance split of a claim's evidence rows; null when there is nothing to split. */
export function evidenceTrustLine(
  relationship: RelationshipEvidenceLike | null | undefined
): string | null {
  if (!relationship) return null;
  const verified = relationship.verified_evidence_count ?? 0;
  const synthetic = relationship.synthetic_evidence_count ?? 0;
  const unknown = relationship.unknown_evidence_count ?? 0;
  if (verified + synthetic + unknown === 0) return null;
  const parts: string[] = [];
  if (verified) parts.push(`${verified} 条已验证来源`);
  if (synthetic) parts.push(`${synthetic} 条 Mock/桩来源`);
  if (unknown) parts.push(`${unknown} 条来源不明`);
  return parts.join(" · ");
}

export interface MetricsLike {
  edge_source_composition?: Record<string, number> | null;
  verified_edge_count?: number | null;
  synthetic_edge_count?: number | null;
  unknown_edge_count?: number | null;
  metrics_basis?: string | null;
}

/**
 * Warning shown next to SNA metrics.
 *
 * Centrality is measured over every edge in scope, mock edges included, so a "central
 * figure" can be an artefact of the extractor. The rule is stated instead of silently
 * changing the numbers, because the metrics must describe the same subgraph the canvas
 * draws.
 */
export function metricsNotice(summary: MetricsLike | null | undefined): string | null {
  if (!summary) return null;
  const synthetic = summary.synthetic_edge_count ?? 0;
  const unknown = summary.unknown_edge_count ?? 0;
  if (synthetic + unknown === 0) return summary.metrics_basis || null;
  const parts: string[] = [];
  if (synthetic) parts.push(`${synthetic} 条 Mock/桩关系`);
  if (unknown) parts.push(`${unknown} 条来源不明关系`);
  return `当前指标包含 ${parts.join("、")}，仅反映抽取结果，不能作为历史影响力结论。`;
}

// ------------------------------------------------------------- person mentions

export interface PersonMentionCountsLike {
  in_group_mention_count?: number | null;
  unlinked_name_match_count?: number | null;
}

export interface PersonMentionNotice {
  tone: "idle" | "warn";
  text: string;
}

/**
 * What the person drawer says about a person's presence in the group.
 *
 * The drawer used to print "该人物在本组没有直接提及记录" whenever the *linked* mention
 * count was zero, which read as "this person never appears in the sources". Two very
 * different situations produce a zero there: the name genuinely is absent, or the name
 * is present but identity linking has not run yet. Only the first one may be stated as
 * a fact.
 */
export function personMentionNotice(
  counts: PersonMentionCountsLike | null | undefined
): PersonMentionNotice {
  const linked = counts?.in_group_mention_count ?? 0;
  const unlinked = counts?.unlinked_name_match_count ?? 0;
  if (linked > 0) {
    return { tone: "idle", text: "" };
  }
  if (unlinked > 0) {
    return {
      tone: "warn",
      text: `本组尚无与本人身份链接的提及，但有 ${unlinked} 条同名提及尚未完成身份链接（不是“档案里没有这个人”）。`,
    };
  }
  return { tone: "idle", text: "该人物在本组确实没有同名提及记录。" };
}

/** Header line: distinguishes linked mentions from same-name unlinked evidence. */
export function personMentionHeadline(
  counts: PersonMentionCountsLike | null | undefined
): string {
  const linked = counts?.in_group_mention_count ?? 0;
  const unlinked = counts?.unlinked_name_match_count ?? 0;
  if (unlinked > 0) {
    return `组内提及 ${linked} 条 · 同名未链接 ${unlinked} 条`;
  }
  return `组内提及 ${linked} 条`;
}

// ------------------------------------------------------------ relationship panel

export interface RelationshipPanelLike {
  id: number;
  source_person_id: number;
  target_person_id: number;
  directionality?: string | null;
  time_description?: string | null;
}

export interface RelationshipPanelFacts {
  directionLabel: string;
  timeLabel: string;
  parallelLabel: string | null;
}

/**
 * Extra facts shown when a relationship is selected (from the canvas or the sidebar).
 *
 * Three things the panel used to hide: whether the claim is directional, whether it has
 * any usable date (the year filter only works on dated relationships), and whether the
 * same pair of people carries several parallel claims — in that case "the" edge the user
 * clicked is one of N, and saying so prevents a wrong reading of the graph.
 */
export function relationshipPanelFacts(
  relationship: RelationshipPanelLike | null | undefined,
  siblings: RelationshipPanelLike[] | null | undefined
): RelationshipPanelFacts {
  if (!relationship) {
    return { directionLabel: "—", timeLabel: "—", parallelLabel: null };
  }
  const undirected = relationship.directionality === "undirected";
  const directionLabel = undirected ? "无向（A—B）" : "有向（箭头由源指向目标）";
  const time = (relationship.time_description || "").trim();
  const timeLabel = time
    ? time
    : "无日期信息（年份筛选不会作用于该关系）";

  let parallelLabel: string | null = null;
  const pairKey = [
    Math.min(relationship.source_person_id, relationship.target_person_id),
    Math.max(relationship.source_person_id, relationship.target_person_id),
  ].join("-");
  const samePair = (siblings ?? [])
    .filter(
      (item) =>
        [
          Math.min(item.source_person_id, item.target_person_id),
          Math.max(item.source_person_id, item.target_person_id),
        ].join("-") === pairKey
    )
    .sort((a, b) => a.id - b.id);
  if (samePair.length > 1) {
    const index = samePair.findIndex((item) => item.id === relationship.id) + 1;
    parallelLabel = `同一对人物共 ${samePair.length} 条关系，这是第 ${index} 条`;
  }

  return { directionLabel, timeLabel, parallelLabel };
}

// --------------------------------------------------------------- role coverage

/** Legend order = the order the palette is declared in; keeps colours stable. */
export const ROLE_CATEGORY_ORDER: string[] = [
  "hong_merchant",
  "foreign_merchant",
  "official",
  "diplomatic",
  "interpreter",
  "comprador",
  "unknown",
];

export interface RoleLegendItem {
  key: string;
  count: number;
}

/**
 * Legend entries for the current view.
 *
 * Only categories that actually occur are returned (with their counts), so the legend
 * cannot advertise an identity class that is not on screen. ``unknown`` participates
 * like any other category instead of being silently folded into a catch-all "other".
 */
export function roleLegendItems(
  roleCounts: Record<string, number> | null | undefined
): RoleLegendItem[] {
  if (!roleCounts) return [];
  return ROLE_CATEGORY_ORDER.filter((key) => (roleCounts[key] ?? 0) > 0).map((key) => ({
    key,
    count: roleCounts[key],
  }));
}

/** "人物 367 人 · 已知角色 0 人 · 角色未知 367 人" — coverage, never a fake total. */
export function roleCoverageLabel(
  totalPersons: number | null | undefined,
  knownRoleCount: number | null | undefined,
  unknownRoleCount: number | null | undefined
): string {
  const total = totalPersons ?? 0;
  const known = knownRoleCount ?? 0;
  const unknown = unknownRoleCount ?? 0;
  return `人物 ${total} 人 · 已知角色 ${known} 人 · 角色未知 ${unknown} 人`;
}

// ------------------------------------------------------------------- run history

export interface RunsSummaryLike {
  active_run?: ExtractionRunLike | null;
  paused_run?: ExtractionRunLike | null;
  last_finished_run?: ExtractionRunLike | null;
  failed_run?: ExtractionRunLike | null;
  primary_run?: ExtractionRunLike | null;
  recent_runs?: ExtractionRunLike[];
}

export interface RunRoleLine {
  role: "active" | "paused" | "finished" | "failed";
  label: string;
  run: ExtractionRunLike;
}

/**
 * Describe every role at once. A paused attempt must not hide a finished run: both are
 * shown, each with its own id and progress.
 */
export function runRoleLines(summary: RunsSummaryLike | null | undefined): RunRoleLine[] {
  if (!summary) return [];
  const lines: RunRoleLine[] = [];
  if (summary.active_run) {
    lines.push({ role: "active", label: "进行中", run: summary.active_run });
  }
  if (summary.paused_run) {
    lines.push({ role: "paused", label: "已暂停", run: summary.paused_run });
  }
  if (summary.last_finished_run) {
    lines.push({ role: "finished", label: "最近完成", run: summary.last_finished_run });
  }
  if (summary.failed_run) {
    lines.push({ role: "failed", label: "失败", run: summary.failed_run });
  }
  return lines;
}

/** Budget provenance must be visible; a historical NULL budget is "unknown", not zero. */
export function budgetSourceLabel(run: ExtractionRunLike | null | undefined): string {
  if (!run) return "未知";
  if (run.budget_blocks == null) return "未设置上限（历史任务）";
  switch (run.budget_source) {
    case "explicit":
      return `人工指定 ${run.budget_blocks} 个区块`;
    case "default":
      return `默认上限 ${run.budget_blocks} 个区块`;
    case "auto":
      return `自动研究上限 ${run.budget_blocks} 个区块`;
    default:
      return `${run.budget_blocks} 个区块（来源未知）`;
  }
}

/** Human labels for the cleanup preview. Unknown keys fall back to the raw key. */
export const CLEANUP_COUNT_LABELS: Record<string, string> = {
  target_runs: "目标运行数",
  mentions_deletable: "可清理的人物提及（可证明属于 Mock 运行）",
  mentions_ambiguous: "来源不确定的提及（保留）",
  mentions_protected_referenced: "被研究记录直接引用的提及（保留）",
  evidence_deletable: "可清理的关系证据（可证明属于 Mock 运行）",
  evidence_ambiguous: "来源不确定的关系证据（保留）",
  evidence_protected_referenced: "被研究记录直接引用的证据（保留）",
  relationships_deletable: "可清理的关系",
  relationships_protected: "必须保留的关系",
  relationships_partially_cleaned: "关系保留但证据可清理",
  persons_deletable: "可清理的孤立人物",
  persons_protected: "必须保留的人物",
  shared_documents: "与其他文档组共享的文档",
  research_sessions_scanned: "扫描的研究记录",
  research_blocks_referenced: "被研究记录引用的区块",
  research_relationships_referenced: "被研究记录引用的关系",
  research_mentions_referenced: "被研究记录按 id 引用的提及",
  research_evidences_referenced: "被研究记录按 id 引用的证据",
  research_referenced_blocks_in_scope: "本组内被研究记录引用的区块",
  research_cited_blocks_with_mock_records: "其中带 Mock 派生记录的引用区块",
};

export function cleanupCountLabel(key: string): string {
  return CLEANUP_COUNT_LABELS[key] ?? key;
}

/** Why a relationship survived the cleanup, in plain words. */
export const RELATIONSHIP_PROTECTION_LABELS: Record<string, string> = {
  accepted_claim: "已审核接受的关系，永不删除",
  referenced_by_research_history: "被保存的研究记录直接引用",
  has_unprovable_evidence: "仍有来源无法证明的证据，关系保留",
  no_evidence_to_verify: "没有任何证据可用于证明归属",
};

export function relationshipProtectionLabel(reason: string): string {
  return RELATIONSHIP_PROTECTION_LABELS[reason] ?? reason;
}

/**
 * Should the extraction panel start expanded?
 *
 * It starts expanded only when the group has no run history at all: that is the single
 * situation where the panel holds the only way to start research. Once a run exists the
 * panel is an operations detail, and leaving it expanded pushed the graph canvas off the
 * screen (the reported "cannot be collapsed" problem, which was a missing control).
 */
export function extractionPanelDefaultOpen(scope: GroupScopeLike | null | undefined): boolean {
  if (!scope) return false;
  const runs = scope.extraction_runs ?? null;
  if (!runs) return true;
  const hasHistory =
    (runs.recent_runs?.length ?? 0) > 0 ||
    runs.active_run != null ||
    runs.paused_run != null ||
    runs.last_finished_run != null ||
    runs.failed_run != null;
  return !hasHistory;
}
