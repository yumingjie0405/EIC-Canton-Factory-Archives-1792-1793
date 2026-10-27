"use client";

import React, { useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  ChevronUp,
  Loader2,
  PauseCircle,
  PlayCircle,
  RefreshCw,
  Send,
  Trash2,
} from "lucide-react";
import { kgApi } from "@/lib/kgApi";
import { GroupGraphScope, KGExtractionRunStatus } from "@/types/kg";
import {
  ExtractionRunLike,
  RunsSummaryLike,
  autoResearchHeadline,
  autoResearchTone,
  budgetSourceLabel,
  canStartExtraction,
  cleanupCountLabel,
  dispatchWarning,
  extractionPanelDefaultOpen,
  isRunActive,
  isRunPaused,
  relationshipProtectionLabel,
  resumeNeedsBudget,
  runBudgetLabel,
  runProgressLabel,
  runRoleLines,
  runStatusLabel,
} from "@/lib/graphScope";

/**
 * Extraction controls for one document group.
 *
 * Everything shown here comes from the persistent ledger (`kg_extraction_runs`):
 * status, attempted/total blocks, tokens spent and the pause reason. No percentage is
 * invented, and a button is only rendered when the backend can actually perform the
 * transition (a budget pause needs a new ceiling before it can resume).
 */
export function ExtractionPanel({
  scope,
  documentId,
  documentName,
  onChanged,
  showAutoResearch = true,
}: {
  scope: GroupGraphScope;
  /** Restricts the run to a single document (used by the group detail page). */
  documentId?: number;
  documentName?: string;
  onChanged: () => void | Promise<void>;
  /** The document-group page shows the auto-research banner once at the top. */
  showAutoResearch?: boolean;
}) {
  const run = (scope.extraction_run ?? null) as KGExtractionRunStatus | null;
  const runLike = run as ExtractionRunLike | null;
  const runsSummary = (scope.extraction_runs ?? null) as RunsSummaryLike | null;
  const roleLines = runRoleLines(runsSummary);
  // Collapsed by default once the group has run history: the panel is an operations
  // detail, and an always-expanded block pushed the graph canvas off-screen.
  const [isOpen, setIsOpen] = useState(() => extractionPanelDefaultOpen(scope));
  const [isBusy, setIsBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [budgetBlocks, setBudgetBlocks] = useState("");
  const [newBudget, setNewBudget] = useState("");
  const [resetPreview, setResetPreview] = useState<{
    counts: Record<string, number>;
    protectedRelationships: { id: number; reason: string; deletable_evidence?: number; kept_evidence?: number }[];
    partiallyCleaned: number[];
  } | null>(null);

  const scopeMatchesDocument =
    documentId == null ||
    (run != null && (run.document_id ?? null) === documentId) ||
    (run != null && run.document_id == null);

  const start = async () => {
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      const parsed = budgetBlocks.trim() === "" ? undefined : Number(budgetBlocks);
      if (parsed !== undefined && (!Number.isFinite(parsed) || parsed <= 0)) {
        setError("区块预算必须是正整数。");
        return;
      }
      const created = await kgApi.startGroupExtraction(scope.group_id, {
        documentId,
        budgetBlocks: parsed,
      });
      setNotice(
        `已创建抽取任务 #${created.run_id}（${created.total_blocks} 个区块），状态：${runStatusLabel(
          created.status
        )}。`
      );
      await onChanged();
    } catch (err: any) {
      setError(err?.message || "启动抽取失败。");
    } finally {
      setIsBusy(false);
    }
  };

  const pause = async () => {
    if (!run) return;
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      await kgApi.pauseExtractionRun(run.run_id);
      setNotice(`任务 #${run.run_id} 已暂停，已完成的工作会保留。`);
      await onChanged();
    } catch (err: any) {
      setError(err?.message || "暂停失败。");
    } finally {
      setIsBusy(false);
    }
  };

  const previewCleanup = async () => {
    setIsBusy(true);
    setError(null);
    setNotice(null);
    setResetPreview(null);
    try {
      const result = await kgApi.resetGroupExtraction(scope.group_id, { dryRun: true });
      setResetPreview({
        counts: result.counts,
        protectedRelationships: result.relationships_protected ?? [],
        partiallyCleaned: result.relationships_partially_cleaned ?? [],
      });
      setNotice("已生成清理预览（未删除任何数据）。执行需人工在服务端确认。");
    } catch (err: any) {
      setError(err?.message || "清理预览失败。");
    } finally {
      setIsBusy(false);
    }
  };

  const redispatch = async () => {    if (!run) return;
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      await kgApi.redispatchExtractionRun(run.run_id);
      setNotice(`任务 #${run.run_id} 已重新投递到队列。`);
      await onChanged();
    } catch (err: any) {
      setError(err?.message || "重新投递失败：队列仍然不可用。");
    } finally {
      setIsBusy(false);
    }
  };

  const resume = async () => {
    if (!run) return;
    setIsBusy(true);
    setError(null);
    setNotice(null);
    try {
      const parsed = newBudget.trim() === "" ? undefined : Number(newBudget);
      if (resumeNeedsBudget(runLike) && parsed === undefined) {
        setError("该任务因预算暂停，恢复前必须填写新的区块上限。");
        return;
      }
      if (parsed !== undefined && (!Number.isFinite(parsed) || parsed <= 0)) {
        setError("新的区块上限必须是正整数。");
        return;
      }
      await kgApi.resumeExtractionRun(run.run_id, {
        budgetBlocks: resumeNeedsBudget(runLike) ? parsed : undefined,
      });
      setNotice(`任务 #${run.run_id} 已恢复，进度从台账继续。`);
      await onChanged();
    } catch (err: any) {
      setError(err?.message || "恢复失败。");
    } finally {
      setIsBusy(false);
    }
  };

  const canStart = canStartExtraction(scope.extraction_state);
  const budgetLabel = runLike ? runBudgetLabel(runLike) : null;
  const auto = scope.auto_research ?? null;
  const warning = dispatchWarning(runLike);
  const summaryText = runLike ? runProgressLabel(runLike) : null;

  return (
    <section
      className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700"
      data-extraction-panel="true"
      data-extraction-state={scope.extraction_state}
      data-auto-research={auto?.effective ? "on" : "off"}
      data-extraction-panel-open={isOpen ? "true" : "false"}
    >
      {/* 折叠头：始终显示一行状态摘要，点右侧按钮展开/收起明细 */}
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => setIsOpen((prev) => !prev)}
          aria-expanded={isOpen}
          aria-controls="extraction-panel-body"
          className="flex items-center gap-1 rounded-md px-1 py-0.5 font-medium text-slate-800 hover:bg-slate-100"
          data-extraction-toggle="true"
          title={isOpen ? "收起抽取明细" : "展开抽取明细"}
        >
          {isOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          {documentId == null ? "组级图谱抽取" : `单篇文档抽取：${documentName ?? `#${documentId}`}`}
        </button>
        <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px]">
          {run ? runStatusLabel(run.status) : "尚未开始"}
        </span>
        {summaryText && (
          <span className="text-[10px] text-slate-500" data-extraction-summary="true">
            {summaryText}
          </span>
        )}
        {!isOpen && (
          <span className="truncate text-[10px] text-slate-400" title={scope.state_message}>
            {scope.state_message}
          </span>
        )}
        {run && scopeMatchesDocument === false && (
          <span className="text-[10px] text-slate-400">
            （显示最近一次任务 # {run.run_id}）
          </span>
        )}
      </div>

      {isOpen && (
        <div id="extraction-panel-body" className="mt-2 space-y-2">
      {showAutoResearch && auto && (
        <div
          className={`flex flex-wrap items-center gap-2 rounded-lg border px-2 py-1 text-[11px] ${
            autoResearchTone(auto) === "ok"
              ? "border-emerald-200 bg-emerald-50 text-emerald-800"
              : autoResearchTone(auto) === "warn"
                ? "border-amber-200 bg-amber-50 text-amber-800"
                : "border-slate-200 bg-slate-50 text-slate-600"
          }`}
          data-auto-research-banner={auto.reason}
        >
          <span className="font-medium">{autoResearchHeadline(auto)}</span>
          <span className="text-[10px] opacity-80">{auto.detail}</span>
        </div>
      )}

      <p className="text-[11px] text-slate-600">{scope.state_message}</p>

      {/* 多条运行记录按角色并列：暂停的旧任务不能掩盖已完成的新任务 */}
      {roleLines.length > 0 && (
        <ul className="space-y-0.5" data-extraction-runs="true">
          {roleLines.map(({ role, label, run: item }) => (
            <li
              key={`${role}-${item.run_id}`}
              data-run-role={role}
              data-run-id={item.run_id}
              className="flex flex-wrap items-center gap-2 text-[11px] text-slate-600"
            >
              <span className="rounded border border-slate-200 bg-slate-50 px-1.5 py-0.5 text-[10px]">
                {label}
              </span>
              <span className="font-medium">#{item.run_id}</span>
              <span>{runStatusLabel(item.status)}</span>
              <span className="text-slate-400">
                {item.attempted_blocks} / {item.total_blocks} 区块
              </span>
              {item.is_mock_model && (
                <span className="rounded bg-amber-100 px-1 py-0.5 text-[10px] text-amber-800">
                  Mock 模型
                </span>
              )}
              <span className="text-[10px] text-slate-400">
                预算：{budgetSourceLabel(item)}
              </span>
            </li>
          ))}
        </ul>
      )}

      {runLike && (
        <div className="space-y-1" data-extraction-progress="true">
          <p>{runProgressLabel(runLike)}</p>
          {budgetLabel && <p className="text-[10px] text-slate-500">预算：{budgetLabel}</p>}
          {run?.pause_reason && (
            <p className="text-[10px] text-amber-700">暂停原因：{run.pause_reason}</p>
          )}
          {run?.trigger && (
            <p className="text-[10px] text-slate-400">
              触发方式：
              {run.trigger === "auto_import"
                ? "导入文档后自动排队"
                : run.trigger === "auto_attach"
                  ? "新加入组后自动排队"
                  : run.trigger === "recovery"
                    ? "恢复投递"
                    : "人工启动"}
            </p>
          )}
          {run?.model && <p className="text-[10px] text-slate-400">模型：{run.model}</p>}
        </div>
      )}

      {/* 图谱覆盖度：区块层面的真实计数，而不是估算的百分比 */}
      <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px] text-slate-500" data-graph-counts="true">
        <span>组内文档 {scope.document_count} 篇</span>
        <span>文本区块 {scope.block_count}</span>
        <span>已抽取区块 {scope.blocks_with_extraction ?? scope.blocks_with_mentions}</span>
        <span>人物提及 {scope.mention_count}</span>
        <span>候选人物 {scope.person_count}</span>
        <span>正式关系 {scope.relationship_count_official}</span>
        <span>候选关系 {scope.relationship_count_candidates}</span>
        <span>待审提及 {scope.pending_mention_reviews}</span>
      </div>

      {warning && (
        <p className="text-[11px] text-rose-700" data-dispatch-warning="true">
          {warning}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {canStart && (
          <>
            <input
              type="number"
              min={1}
              value={budgetBlocks}
              onChange={(event) => setBudgetBlocks(event.target.value)}
              placeholder="区块预算（可选）"
              disabled={isBusy}
              className="w-32 rounded border border-slate-200 px-2 py-1 text-[11px]"
              data-extraction-budget-input="true"
            />
            <button
              type="button"
              disabled={isBusy}
              onClick={start}
              className="flex items-center gap-1 rounded-full bg-slate-900 px-3 py-1 text-[11px] font-medium text-white disabled:opacity-50"
              data-extraction-start="true"
            >
              {isBusy ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <PlayCircle className="h-3 w-3" />
              )}
              <span>{documentId == null ? "开始抽取本组" : "抽取这篇文档"}</span>
            </button>
          </>
        )}

        {isRunActive(runLike) && (
          <button
            type="button"
            disabled={isBusy}
            onClick={pause}
            className="flex items-center gap-1 rounded-full border border-amber-300 bg-amber-50 px-3 py-1 text-[11px] font-medium text-amber-800 disabled:opacity-50"
            data-extraction-pause="true"
          >
            <PauseCircle className="h-3 w-3" />
            <span>暂停</span>
          </button>
        )}

        {isRunPaused(runLike) && (
          <>
            {resumeNeedsBudget(runLike) && (
              <input
                type="number"
                min={1}
                value={newBudget}
                onChange={(event) => setNewBudget(event.target.value)}
                placeholder="新的区块上限"
                disabled={isBusy}
                className="w-32 rounded border border-slate-200 px-2 py-1 text-[11px]"
                data-extraction-newbudget-input="true"
              />
            )}
            <button
              type="button"
              disabled={isBusy}
              onClick={resume}
              className="flex items-center gap-1 rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1 text-[11px] font-medium text-emerald-800 disabled:opacity-50"
              data-extraction-resume="true"
            >
              <RefreshCw className="h-3 w-3" />
              <span>恢复任务</span>
            </button>
          </>
        )}

        {run && run.dispatch_state === "failed" && (
          <button
            type="button"
            disabled={isBusy}
            onClick={redispatch}
            className="flex items-center gap-1 rounded-full border border-rose-300 bg-rose-50 px-3 py-1 text-[11px] font-medium text-rose-800 disabled:opacity-50"
            data-extraction-redispatch="true"
          >
            <Send className="h-3 w-3" />
            <span>重新投递</span>
          </button>
        )}

        {scope.data_trust?.mock_derived && (
          <button
            type="button"
            disabled={isBusy}
            onClick={previewCleanup}
            className="flex items-center gap-1 rounded-full border border-slate-300 bg-white px-3 py-1 text-[11px] font-medium text-slate-700 disabled:opacity-50"
            data-extraction-cleanup-preview="true"
          >
            <Trash2 className="h-3 w-3" />
            <span>预览 Mock 数据清理</span>
          </button>
        )}
      </div>

      {resetPreview && (
        <div
          className="rounded-lg border border-slate-200 bg-slate-50 px-2 py-1.5 text-[11px] text-slate-700"
          data-extraction-cleanup-counts="true"
        >
          <p className="font-medium text-slate-800">
            清理预览（dry-run，未删除任何数据）
          </p>
          <ul className="mt-0.5 grid grid-cols-2 gap-x-3 text-[10px]">
            {Object.entries(resetPreview.counts).map(([key, value]) => (
              <li key={key}>
                {cleanupCountLabel(key)}: {value}
              </li>
            ))}
          </ul>
          {resetPreview.partiallyCleaned.length > 0 && (
            <p className="mt-1 text-[10px] text-amber-700">
              关系 {resetPreview.partiallyCleaned.map((id) => `#${id}`).join("、")} 将保留为研究主张，
              但其下可证明属于 Mock 运行的证据会单独清理，需人工复核这些关系是否也应当删除。
            </p>
          )}
          {resetPreview.protectedRelationships.length > 0 && (
            <ul className="mt-1 text-[10px] text-slate-500">
              {resetPreview.protectedRelationships.map((item) => (
                <li key={item.id}>
                  关系 #{item.id} 保留：{relationshipProtectionLabel(item.reason)}
                  {item.deletable_evidence != null
                    ? `（可清理证据 ${item.deletable_evidence} 条，保留证据 ${item.kept_evidence ?? 0} 条）`
                    : ""}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <p className="text-[10px] text-slate-400">
        抽取只产生候选人物与关系及其区块证据，审核仍是独立的人工步骤；不会自动进入正式图谱。
      </p>

      {notice && (
        <p className="text-[11px] text-emerald-700" data-extraction-notice="true">
          {notice}
        </p>
      )}
      {error && (
        <p
          className="flex items-center gap-1 text-[11px] text-rose-700"
          data-extraction-error="true"
        >
          <AlertCircle className="h-3 w-3" />
          {error}
        </p>
      )}
        </div>
      )}
    </section>
  );
}
