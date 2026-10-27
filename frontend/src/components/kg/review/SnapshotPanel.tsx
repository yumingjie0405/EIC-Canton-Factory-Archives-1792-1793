"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertCircle, Camera, GitCompare, Loader2, RefreshCw } from "lucide-react";
import { kgApi } from "@/lib/kgApi";
import type { SnapshotCompare, SnapshotDetail, SnapshotSummary } from "@/types/kgWorkflow";
import { ProvenanceBadge } from "./ProvenanceBadge";

/**
 * Snapshot list, detail and comparison.
 *
 * A snapshot is only trustworthy if it says what it *can* do: the panel shows
 * `capabilities` verbatim (display / rebuild / recompute) instead of implying that a
 * stored row can be replayed exactly.
 */
export function SnapshotPanel({ groupId }: { groupId: number }) {
  const [rows, setRows] = useState<SnapshotSummary[]>([]);
  const [detail, setDetail] = useState<SnapshotDetail | null>(null);
  const [compare, setCompare] = useState<SnapshotCompare | null>(null);
  const [left, setLeft] = useState<string>("");
  const [right, setRight] = useState<string>("");
  const [name, setName] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await kgApi.listGraphSnapshots(groupId, 20);
      setRows(result);
    } catch (err: any) {
      setError(err?.message || "加载快照失败。");
    } finally {
      setIsLoading(false);
    }
  }, [groupId]);

  useEffect(() => {
    load();
  }, [load]);

  const create = async () => {
    setIsLoading(true);
    setError(null);
    setNotice(null);
    try {
      const snapshot = await kgApi.createGraphSnapshot({
        group_id: groupId,
        name: name || undefined,
        view: "all",
        created_by: "reviewer",
      });
      setNotice(
        `快照「${snapshot.run_id}」已保存：${snapshot.node_count} 人物 / ${snapshot.edge_count} 关系。`
      );
      setName("");
      await load();
    } catch (err: any) {
      setError(err?.message || "创建快照失败。");
    } finally {
      setIsLoading(false);
    }
  };

  const openDetail = async (runId: string) => {
    setError(null);
    setCompare(null);
    try {
      setDetail(await kgApi.getGraphSnapshot(runId));
    } catch (err: any) {
      setDetail(null);
      setError(err?.message || "加载快照详情失败。");
    }
  };

  const runCompare = async () => {
    if (!left || !right) {
      setError("请选择两个快照进行比较。");
      return;
    }
    setError(null);
    setDetail(null);
    try {
      setCompare(await kgApi.compareGraphSnapshots(left, right));
    } catch (err: any) {
      setCompare(null);
      setError(err?.message || "比较失败。");
    }
  };

  return (
    <div className="space-y-4" data-snapshot-panel="true">
      <div className="flex flex-wrap items-end gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="min-w-[14rem] flex-1">
          <label className="text-[11px] font-medium text-slate-500">快照名称</label>
          <input
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="例如 1792 年毛织品关系"
            className="mt-1 block w-full rounded-lg border border-slate-300 px-2 py-1 text-xs"
          />
        </div>
        <button
          type="button"
          onClick={create}
          disabled={isLoading}
          className="flex items-center gap-1 rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50"
          data-action="create-snapshot"
        >
          <Camera className="h-3 w-3" /> 保存当前图谱快照
        </button>
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
        <div className="rounded-xl border border-emerald-300 bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          {notice}
        </div>
      )}

      <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <table className="min-w-full text-left text-xs">
          <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
            <tr>
              <th className="px-3 py-2">快照</th>
              <th className="px-3 py-2">视图</th>
              <th className="px-3 py-2">人物 / 关系</th>
              <th className="px-3 py-2">能力</th>
              <th className="px-3 py-2">来源</th>
              <th className="px-3 py-2">时间</th>
              <th className="px-3 py-2">比较</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row) => (
              <tr key={row.run_id} data-snapshot-row={row.run_id}>
                <td className="px-3 py-2">
                  <button
                    type="button"
                    onClick={() => openDetail(row.run_id)}
                    className="text-left font-medium text-indigo-700 hover:underline"
                  >
                    {row.name || row.run_id}
                  </button>
                  <div className="text-[10px] text-slate-400">{row.run_id}</div>
                </td>
                <td className="px-3 py-2 text-slate-700">{row.view}</td>
                <td className="px-3 py-2 text-slate-700">
                  {row.node_count} / {row.edge_count}
                </td>
                <td className="px-3 py-2 text-slate-500">{row.capability}</td>
                <td className="px-3 py-2">
                  <ProvenanceBadge synthetic={row.synthetic} kind={row.synthetic ? "mock_text_derived" : "provider_reported"} />
                </td>
                <td className="px-3 py-2 text-slate-500">{row.created_at?.replace("T", " ").slice(0, 19)}</td>
                <td className="px-3 py-2">
                  <div className="flex gap-1">
                    <button
                      type="button"
                      onClick={() => setLeft(row.run_id)}
                      className={`rounded-md border px-2 py-0.5 text-[10px] ${
                        left === row.run_id ? "border-indigo-400 bg-indigo-50 text-indigo-700" : "border-slate-300 text-slate-600"
                      }`}
                    >
                      左
                    </button>
                    <button
                      type="button"
                      onClick={() => setRight(row.run_id)}
                      className={`rounded-md border px-2 py-0.5 text-[10px] ${
                        right === row.run_id ? "border-indigo-400 bg-indigo-50 text-indigo-700" : "border-slate-300 text-slate-600"
                      }`}
                    >
                      右
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {!isLoading && rows.length === 0 && (
              <tr>
                <td colSpan={7} className="px-3 py-6 text-center text-slate-500">
                  尚无快照。保存后会记录当时的节点、边、过滤参数和来源构成。
                </td>
              </tr>
            )}
          </tbody>
        </table>
        <div className="border-t border-slate-100 px-3 py-2">
          <button
            type="button"
            onClick={runCompare}
            className="flex items-center gap-1 rounded-lg border border-slate-300 px-3 py-1 text-[11px] font-medium text-slate-700"
            data-action="compare-snapshots"
          >
            <GitCompare className="h-3 w-3" /> 比较所选快照（左 vs 右）
          </button>
        </div>
      </div>

      {detail && (
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm" data-snapshot-detail={detail.run_id}>
          <h4 className="text-sm font-semibold text-slate-900">{detail.name || detail.run_id}</h4>
          <p className="text-[11px] text-slate-500">
            {detail.node_count} 人物 / {detail.edge_count} 关系 · 视图 {detail.view} · 算法 {detail.algorithm_version}
          </p>
          <p className="mt-1 text-[11px] text-slate-600">{detail.capabilities?.explanation}</p>
          <div className="mt-2 flex flex-wrap gap-2 text-[10px] text-slate-500">
            <span>显示：{detail.capabilities?.can_display ? "可" : "否"}</span>
            <span>重建：{detail.capabilities?.can_rebuild ? "可" : "否"}</span>
            <span>重算：{detail.capabilities?.can_recompute ? "可" : "否"}</span>
            <span>抽取运行：{detail.extraction_run_ids.length ? detail.extraction_run_ids.map((id) => `#${id}`).join("、") : "无"}</span>
          </div>
          <div className="mt-2">
            <ProvenanceBadge synthetic={detail.synthetic} counts={detail.data_trust?.source_kinds} kind={
              detail.synthetic ? "mock_text_derived" : "provider_reported"
            } />
          </div>
          {detail.data_trust?.note && (
            <p className="mt-1 text-[10px] text-amber-800">{detail.data_trust.note}</p>
          )}
          <ul className="mt-2 max-h-52 space-y-1 overflow-y-auto text-[10px] text-slate-600">
            {detail.relationships.slice(0, 40).map((rel) => (
              <li key={rel.id}>
                #{rel.id} {rel.relation_type} · {rel.claim_status} · 证据 {rel.evidence_count ?? 0}
              </li>
            ))}
          </ul>
        </div>
      )}

      {compare && (
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm" data-snapshot-compare="true">
          <h4 className="text-sm font-semibold text-slate-900">
            比较：{compare.left.run_id} → {compare.right.run_id}
          </h4>
          <div className="mt-2 grid gap-3 md:grid-cols-2">
            <div className="rounded-xl bg-slate-50 p-2 text-[11px]">
              <p className="font-medium text-slate-700">结构变化</p>
              <p>新增人物 {compare.structure.persons_added?.length ?? 0} · 消失人物 {compare.structure.persons_removed?.length ?? 0}</p>
              <p>新增关系 {compare.structure.relationships_added?.length ?? 0} · 消失关系 {compare.structure.relationships_removed?.length ?? 0}</p>
            </div>
            <div className="rounded-xl bg-slate-50 p-2 text-[11px]">
              <p className="font-medium text-slate-700">审核 / 证据 / 来源</p>
              <p>审核变化 {compare.review_changes.length} · 证据数量变化 {compare.evidence_changes.length}</p>
              <p>来源构成变化 {compare.provenance_changes.length}</p>
            </div>
          </div>
          {compare.notes.map((note) => (
            <p key={note} className="mt-2 text-[11px] text-amber-800">
              {note}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
