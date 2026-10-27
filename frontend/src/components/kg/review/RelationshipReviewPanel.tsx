"use client";

import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Check,
  ChevronLeft,
  ChevronRight,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import { kgApi } from "@/lib/kgApi";
import type {
  ClaimStatusValue,
  ReviewRelationshipRow,
  RelationshipReviewList,
  ReviewStatusValue,
} from "@/types/kgWorkflow";
import { ProvenanceBadge, SyntheticWarning } from "./ProvenanceBadge";

const CLAIM_LABELS: Record<string, string> = {
  asserted: "已主张",
  accepted: "已接受",
  uncertain: "不确定",
  contested: "有争议",
  rejected: "已拒绝",
};
const REVIEW_LABELS: Record<string, string> = {
  unreviewed: "未审核",
  in_review: "审核中",
  reviewed: "已审核",
};

/**
 * Relationship review workbench.
 *
 * Rules made visible in the UI:
 *  * the list is evidence-first (a claim the group cannot support never appears);
 *  * acceptance is a separate button from the provenance badge — approving a rule-stub
 *    claim does not turn it into an archival finding, and the warning stays;
 *  * every write is reported from the server response, never from local state.
 */
export function RelationshipReviewPanel({
  groupId,
  groupName,
}: {
  groupId: number;
  groupName?: string;
}) {
  const [data, setData] = useState<RelationshipReviewList | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [claimFilter, setClaimFilter] = useState<string>("");
  const [reviewFilter, setReviewFilter] = useState<string>("");
  const [sourceFilter, setSourceFilter] = useState<string>("");
  const [query, setQuery] = useState("");
  const [skip, setSkip] = useState(0);
  const [selected, setSelected] = useState<ReviewRelationshipRow | null>(null);
  const [evidence, setEvidence] = useState<
    | {
        items: {
          evidence_id: number;
          document_name: string;
          page_number: number;
          block_id: number;
          quote_text?: string | null;
          block_text?: string | null;
          source_kind?: string | null;
          synthetic?: boolean;
        }[];
        in_group_evidence_count?: number;
        out_of_group_evidence_count?: number;
        truncated?: boolean;
      }
    | null
  >(null);
  const [busy, setBusy] = useState(false);
  const limit = 20;

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await kgApi.listGroupRelationships(groupId, {
        claim_status: claimFilter || undefined,
        review_status: reviewFilter || undefined,
        source_kind: (sourceFilter || undefined) as never,
        q: query || undefined,
        skip,
        limit,
      });
      setData(result);
    } catch (err: any) {
      setData(null);
      setError(err?.message || "加载关系候选失败。");
    } finally {
      setIsLoading(false);
    }
  }, [groupId, claimFilter, reviewFilter, sourceFilter, query, skip]);

  useEffect(() => {
    load();
  }, [load]);

  const openEvidence = async (row: ReviewRelationshipRow) => {
    setSelected(row);
    setEvidence(null);
    try {
      const result = await kgApi.getGroupRelationshipEvidence(groupId, row.id);
      setEvidence(result as never);
    } catch (err: any) {
      setError(err?.message || "加载证据失败。");
    }
  };

  const applyReview = async (
    row: ReviewRelationshipRow,
    payload: { claim_status?: ClaimStatusValue; review_status?: ReviewStatusValue }
  ) => {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await kgApi.reviewRelationshipClaim(row.id, payload);
      setNotice(
        `关系 #${row.id} 已更新为 ${CLAIM_LABELS[result.relationship.claim_status] || result.relationship.claim_status} / ${
          REVIEW_LABELS[result.relationship.review_status] || result.relationship.review_status
        }。${result.warning ? " " + result.warning : ""}`
      );
      await load();
      if (selected?.id === row.id) setSelected(result.relationship);
    } catch (err: any) {
      setError(err?.message || "审核失败。");
    } finally {
      setBusy(false);
    }
  };

  const total = data?.total ?? 0;
  const pageInfo = useMemo(
    () => `${total === 0 ? 0 : skip + 1}-${Math.min(skip + limit, total)} / ${total}`,
    [skip, limit, total]
  );

  return (
    <div className="space-y-4" data-relationship-review-panel="true">
      <div className="flex flex-wrap items-end gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div>
          <label className="text-[11px] font-medium text-slate-500">主张状态</label>
          <select
            value={claimFilter}
            onChange={(event) => {
              setSkip(0);
              setClaimFilter(event.target.value);
            }}
            className="mt-1 block rounded-lg border border-slate-300 px-2 py-1 text-xs"
            data-filter="claim_status"
          >
            <option value="">全部</option>
            {Object.entries(CLAIM_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
                {data?.counts.by_claim_status?.[value] ? ` (${data.counts.by_claim_status[value]})` : ""}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-[11px] font-medium text-slate-500">审核状态</label>
          <select
            value={reviewFilter}
            onChange={(event) => {
              setSkip(0);
              setReviewFilter(event.target.value);
            }}
            className="mt-1 block rounded-lg border border-slate-300 px-2 py-1 text-xs"
            data-filter="review_status"
          >
            <option value="">全部</option>
            {Object.entries(REVIEW_LABELS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-[11px] font-medium text-slate-500">来源（数据可信度）</label>
          <select
            value={sourceFilter}
            onChange={(event) => {
              setSkip(0);
              setSourceFilter(event.target.value);
            }}
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
        <div className="min-w-[12rem] flex-1">
          <label className="text-[11px] font-medium text-slate-500">搜索人物/标签</label>
          <input
            value={query}
            onChange={(event) => {
              setSkip(0);
              setQuery(event.target.value);
            }}
            placeholder="例如 Howqua"
            className="mt-1 block w-full rounded-lg border border-slate-300 px-2 py-1 text-xs"
            data-filter="query"
          />
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
        <div className="rounded-xl border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-800" data-review-notice="true">
          {notice}
        </div>
      )}
      <SyntheticWarning note={data?.data_trust?.note} />

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2">关系</th>
              <th className="px-3 py-2">类型</th>
              <th className="px-3 py-2">主张 / 审核</th>
              <th className="px-3 py-2">来源</th>
              <th className="px-3 py-2">证据</th>
              <th className="px-3 py-2">日期</th>
              <th className="px-3 py-2">操作</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {data?.items.map((row) => (
              <tr key={row.id} data-relationship-row={row.id} className="hover:bg-slate-50/60">
                <td className="px-3 py-2">
                  <button
                    type="button"
                    onClick={() => openEvidence(row)}
                    className="text-left font-medium text-indigo-700 hover:underline"
                  >
                    {row.source_name || `#${row.source_person_id}`} → {row.target_name || `#${row.target_person_id}`}
                  </button>
                  <div className="text-[10px] text-slate-400">#{row.id}</div>
                </td>
                <td className="px-3 py-2 text-slate-700">{row.relation_type}</td>
                <td className="px-3 py-2">
                  <div className="text-slate-800">{CLAIM_LABELS[row.claim_status] || row.claim_status}</div>
                  <div className="text-[10px] text-slate-400">{REVIEW_LABELS[row.review_status] || row.review_status}</div>
                </td>
                <td className="px-3 py-2">
                  <ProvenanceBadge kind={row.source_kind} synthetic={row.synthetic} counts={row.source_kinds} />
                </td>
                <td className="px-3 py-2 text-slate-700">{row.evidence_count}</td>
                <td className="px-3 py-2 text-slate-500">
                  {row.has_date ? row.valid_from || row.valid_to || row.time_description : "无日期"}
                </td>
                <td className="px-3 py-2">
                  <div className="flex flex-wrap gap-1">
                    <button
                      type="button"
                      disabled={busy || row.claim_status === "accepted"}
                      onClick={() => applyReview(row, { claim_status: "accepted", review_status: "reviewed" })}
                      className="flex items-center gap-1 rounded-md border border-emerald-300 px-2 py-0.5 text-[10px] text-emerald-700 disabled:opacity-40"
                      data-action="accept"
                    >
                      <Check className="h-3 w-3" /> 接受
                    </button>
                    <button
                      type="button"
                      disabled={busy || row.claim_status === "rejected"}
                      onClick={() => applyReview(row, { claim_status: "rejected", review_status: "reviewed" })}
                      className="flex items-center gap-1 rounded-md border border-rose-300 px-2 py-0.5 text-[10px] text-rose-700 disabled:opacity-40"
                      data-action="reject"
                    >
                      <X className="h-3 w-3" /> 拒绝
                    </button>
                    <button
                      type="button"
                      disabled={busy || row.claim_status === "uncertain"}
                      onClick={() => applyReview(row, { claim_status: "uncertain", review_status: "in_review" })}
                      className="rounded-md border border-amber-300 px-2 py-0.5 text-[10px] text-amber-700 disabled:opacity-40"
                      data-action="defer"
                    >
                      搁置
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {!isLoading && (data?.items.length ?? 0) === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-slate-500">
                  当前筛选下没有关系候选。空结果不等于全局数据。
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="flex items-center justify-between border-t border-slate-100 px-3 py-2 text-[11px] text-slate-500">
          <span>{pageInfo}</span>
          <div className="flex gap-1">
            <button
              type="button"
              disabled={skip === 0 || isLoading}
              onClick={() => setSkip(Math.max(0, skip - limit))}
              className="rounded-md border border-slate-300 px-2 py-0.5 disabled:opacity-40"
            >
              <ChevronLeft className="h-3 w-3" />
            </button>
            <button
              type="button"
              disabled={skip + limit >= total || isLoading}
              onClick={() => setSkip(skip + limit)}
              className="rounded-md border border-slate-300 px-2 py-0.5 disabled:opacity-40"
            >
              <ChevronRight className="h-3 w-3" />
            </button>
          </div>
        </div>
      </div>

      {selected && (
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm" data-evidence-panel={selected.id}>
          <div className="mb-2 flex items-start justify-between">
            <div>
              <h4 className="text-sm font-semibold text-slate-900">
                关系 #{selected.id}：{selected.source_name || `#${selected.source_person_id}`} →{" "}
                {selected.target_name || `#${selected.target_person_id}`}
              </h4>
              <p className="text-[11px] text-slate-500">
                {selected.relation_type} · {CLAIM_LABELS[selected.claim_status] || selected.claim_status} ·{" "}
                {REVIEW_LABELS[selected.review_status] || selected.review_status}
              </p>
            </div>
            <button type="button" onClick={() => setSelected(null)} className="text-slate-400 hover:text-slate-600">
              <X className="h-4 w-4" />
            </button>
          </div>
          <ProvenanceBadge kind={selected.source_kind} synthetic={selected.synthetic} counts={selected.source_kinds} />
          {!evidence && <p className="mt-2 text-[11px] text-slate-500">正在加载证据…</p>}
          {evidence && (
            <>
              <p className="mt-2 text-[11px] text-slate-600">
                组内证据 {evidence.in_group_evidence_count ?? evidence.items.length} 条
                {evidence.truncated ? `（展示前 ${evidence.items.length} 条）` : ""}
                {evidence.out_of_group_evidence_count
                  ? `（另有 ${evidence.out_of_group_evidence_count} 条属于其他文档组，未计入本组）`
                  : ""}
              </p>
              <ul className="mt-2 space-y-2">
                {evidence.items.map((item) => (
                  <li key={item.evidence_id} className="rounded-xl border border-slate-200 p-2">
                    <p className="text-[10px] text-slate-400">
                      {item.document_name} · 第 {item.page_number} 页 · Block #{item.block_id} · 证据 #{item.evidence_id}
                    </p>
                    <p className="mt-1 text-[11px] text-slate-700">引文：{item.quote_text || "（无引文）"}</p>
                    <p className="mt-1 line-clamp-3 text-[10px] text-slate-500">{item.block_text}</p>
                    <div className="mt-1">
                      <ProvenanceBadge kind={item.source_kind} synthetic={item.synthetic} />
                    </div>
                  </li>
                ))}
                {evidence.items.length === 0 && (
                  <li className="rounded-xl bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800">
                    该关系在本组没有可追溯证据：全局审核状态不能替代当前组的证据资格。
                  </li>
                )}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
