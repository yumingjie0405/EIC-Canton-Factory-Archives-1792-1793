"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertCircle, Check, FlaskConical, Loader2, RefreshCw, Undo2, Users } from "lucide-react";
import { kgApi } from "@/lib/kgApi";
import type { BatchLinkPreview, MentionCluster, MentionClusterList } from "@/types/kgWorkflow";
import { ProvenanceBadge, SyntheticWarning } from "./ProvenanceBadge";

/**
 * Disambiguation workbench with a two-step batch flow.
 *
 * Step 1 previews exactly which mentions would be linked and how much of the cluster is
 * stub output; step 2 applies it with the preview token, so a stale preview can never be
 * silently applied to a changed set. Every confirmation returns its `batch_id`, which
 * can be reverted from the UI.
 */
export function DisambiguationPanel({ groupId }: { groupId: number }) {
  const [data, setData] = useState<MentionClusterList | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sourceFilter, setSourceFilter] = useState("");
  const [selected, setSelected] = useState<MentionCluster | null>(null);
  const [targetName, setTargetName] = useState("");
  const [preview, setPreview] = useState<BatchLinkPreview | null>(null);
  const [busy, setBusy] = useState(false);
  const [batches, setBatches] = useState<{ batch_id: string; linked: number; name: string }[]>([]);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await kgApi.listMentionClusters(groupId, {
        only_unresolved: true,
        source_kind: (sourceFilter || undefined) as never,
        limit_groups: 50,
      });
      setData(result);
    } catch (err: any) {
      setData(null);
      setError(err?.message || "加载提及聚类失败。");
    } finally {
      setIsLoading(false);
    }
  }, [groupId, sourceFilter]);

  useEffect(() => {
    load();
  }, [load]);

  const runPreview = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    setPreview(null);
    try {
      const result = await kgApi.previewMentionBatchLink(groupId, {
        normalized_text: selected.normalized_text,
        new_person_name: targetName || selected.normalized_text,
      });
      setPreview(result);
      setNotice("预览已生成，尚未写入任何数据。请核对命中范围后确认。");
    } catch (err: any) {
      setError(err?.message || "生成预览失败。");
    } finally {
      setBusy(false);
    }
  };

  const confirm = async () => {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const result = await kgApi.confirmMentionBatchLink(groupId, {
        normalized_text: preview.normalized_text,
        confirm_token: preview.confirm_token,
        new_person_name: preview.target_person_name,
        reason: "工作台批量消歧",
      });
      setNotice(
        `已把 ${result.mentions_linked} 条提及关联到「${result.person_name}」${
          result.created_person ? "（新建人物）" : "（复用已有身份）"
        }。批次 ${result.batch_id} 可撤销。`
      );
      setBatches((prev) => [
        { batch_id: result.batch_id, linked: result.mentions_linked, name: result.person_name },
        ...prev,
      ]);
      setPreview(null);
      setSelected(null);
      setTargetName("");
      await load();
    } catch (err: any) {
      setError(err?.message || "确认失败（预览可能已过期）。");
    } finally {
      setBusy(false);
    }
  };

  const revert = async (batchId: string) => {
    setBusy(true);
    setError(null);
    try {
      const result = await kgApi.revertMentionBatch(batchId, "工作台撤销");
      setNotice(`批次 ${batchId} 已撤销，恢复 ${result.restored_mentions} 条提及。`);
      setBatches((prev) => prev.filter((item) => item.batch_id !== batchId));
      await load();
    } catch (err: any) {
      setError(err?.message || "撤销失败。");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4" data-disambiguation-panel="true">
      <div className="flex flex-wrap items-end gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div>
          <label className="text-[11px] font-medium text-slate-500">来源过滤</label>
          <select
            value={sourceFilter}
            onChange={(event) => setSourceFilter(event.target.value)}
            className="mt-1 block rounded-lg border border-slate-300 px-2 py-1 text-xs"
            data-filter="source_kind"
          >
            <option value="">全部</option>
            <option value="provider_reported">真实模型</option>
            <option value="historical_seed">人工种子</option>
            <option value="mock_text_derived">规则桩</option>
            <option value="mock_constant">固定 Mock</option>
            <option value="unknown">来源不明</option>
          </select>
        </div>
        <div className="text-[11px] text-slate-500">
          待处理提及 <strong>{data?.total_mentions ?? 0}</strong> 条 · 名称簇{" "}
          <strong>{data?.total_clusters ?? 0}</strong> 个
        </div>
        <button
          type="button"
          onClick={load}
          disabled={isLoading}
          className="flex items-center gap-1 rounded-lg border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 disabled:opacity-50"
        >
          {isLoading ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}
          刷新
        </button>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-xl border border-rose-300 bg-rose-50 px-3 py-2 text-xs text-rose-800">
          <AlertCircle className="h-3.5 w-3.5" />
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-xl border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-800" data-batch-notice="true">
          {notice}
        </div>
      )}
      <SyntheticWarning note={data?.data_trust?.note} />

      {batches.length > 0 && (
        <ul className="space-y-1 rounded-2xl border border-slate-200 bg-white p-3 text-[11px]">
          {batches.map((item) => (
            <li key={item.batch_id} className="flex items-center justify-between">
              <span>
                批次 {item.batch_id.slice(0, 8)}… · {item.name} · {item.linked} 条
              </span>
              <button
                type="button"
                onClick={() => revert(item.batch_id)}
                disabled={busy}
                className="flex items-center gap-1 rounded-md border border-slate-300 px-2 py-0.5 text-[10px] text-slate-700 disabled:opacity-40"
                data-action="revert-batch"
              >
                <Undo2 className="h-3 w-3" /> 撤销
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="border-b border-slate-100 px-3 py-2 text-[11px] font-semibold text-slate-600">
            名称簇（同名不等于同一人）
          </div>
          <ul className="max-h-[26rem] divide-y divide-slate-100 overflow-y-auto">
            {data?.items.map((cluster) => (
              <li key={cluster.normalized_text} className="px-3 py-2">
                <button
                  type="button"
                  onClick={() => {
                    setSelected(cluster);
                    setTargetName(cluster.normalized_text);
                    setPreview(null);
                    setNotice(null);
                  }}
                  className="flex w-full items-start justify-between gap-2 text-left"
                  data-cluster={cluster.normalized_text}
                >
                  <div>
                    <div className="flex items-center gap-2 text-xs font-medium text-slate-900">
                      <Users className="h-3 w-3 text-slate-400" />
                      {cluster.normalized_text || "（空名称）"}
                    </div>
                    <div className="text-[10px] text-slate-500">
                      {cluster.mention_count} 条提及 · {cluster.block_count} 个区块
                      {cluster.resolved_person_names.length > 0
                        ? ` · 已关联：${cluster.resolved_person_names.join("、")}`
                        : ""}
                    </div>
                  </div>
                  <ProvenanceBadge synthetic={cluster.synthetic} counts={cluster.source_kinds} kind={
                    Object.keys(cluster.source_kinds)[0] ?? "unknown"
                  } />
                </button>
              </li>
            ))}
            {!isLoading && (data?.items.length ?? 0) === 0 && (
              <li className="px-3 py-6 text-center text-[11px] text-slate-500">
                当前没有待消歧的提及。
              </li>
            )}
          </ul>
        </div>

        <div className="space-y-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <h4 className="text-sm font-semibold text-slate-900">批量处理（先预览，再确认）</h4>
          {!selected && <p className="text-[11px] text-slate-500">请先在左侧选择一个名称簇。</p>}
          {selected && (
            <>
              <div>
                <label className="text-[11px] font-medium text-slate-500">目标人物</label>
                <input
                  value={targetName}
                  onChange={(event) => setTargetName(event.target.value)}
                  className="mt-1 block w-full rounded-lg border border-slate-300 px-2 py-1 text-xs"
                  data-field="target_person_name"
                />
                <p className="mt-1 text-[10px] text-slate-400">
                  留空则使用簇名称；同名现有身份会被复用，不会重复创建。
                </p>
              </div>
              <ul className="space-y-1 text-[10px] text-slate-500">
                {selected.sample_mentions.map((mention) => (
                  <li key={mention.mention_id} className="rounded-lg bg-slate-50 px-2 py-1">
                    Block #{mention.block_id} · {mention.raw_text}
                    {mention.temporal_year ? ` · ${mention.temporal_year}` : ""}
                  </li>
                ))}
              </ul>
              <button
                type="button"
                onClick={runPreview}
                disabled={busy}
                className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                data-action="preview-batch"
              >
                {busy ? "处理中…" : "生成预览"}
              </button>

              {preview && (
                <div className="space-y-2 rounded-xl border border-indigo-200 bg-indigo-50/60 p-3" data-batch-preview="true">
                  <p className="text-[11px] font-medium text-indigo-900">
                    将关联 {preview.mention_count} 条提及 → 「{preview.target_person_name}」
                    {preview.create_new_person ? "（新建人物）" : "（复用已有身份）"}
                  </p>
                  {preview.years.length > 0 && (
                    <p className="text-[10px] text-indigo-800">涉及年份：{preview.years.join("、")}</p>
                  )}
                  <ProvenanceBadge synthetic={preview.synthetic} counts={preview.source_kinds} kind={
                    Object.keys(preview.source_kinds)[0] ?? "unknown"
                  } />
                  {preview.warnings.map((warning) => (
                    <p key={warning} className="flex items-start gap-1 text-[10px] text-amber-800">
                      <FlaskConical className="mt-0.5 h-3 w-3 shrink-0" />
                      {warning}
                    </p>
                  ))}
                  <button
                    type="button"
                    onClick={confirm}
                    disabled={busy}
                    className="flex items-center gap-1 rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
                    data-action="confirm-batch"
                  >
                    <Check className="h-3 w-3" /> 确认执行
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
