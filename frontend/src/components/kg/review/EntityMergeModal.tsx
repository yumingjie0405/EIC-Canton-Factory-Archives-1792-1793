"use client";

import React, { useState } from "react";
import { X, GitMerge, AlertTriangle, CheckCircle2, ArrowRight, ShieldCheck } from "lucide-react";
import { MergePreviewResponse } from "@/types/kg";
import { kgApi } from "@/lib/kgApi";

interface EntityMergeModalProps {
  isOpen: boolean;
  onClose: () => void;
  onMerged?: () => void;
}

export function EntityMergeModal({ isOpen, onClose, onMerged }: EntityMergeModalProps) {
  const [primaryId, setPrimaryId] = useState("");
  const [secondaryId, setSecondaryId] = useState("");
  const [operator, setOperator] = useState("Dr. Historian");
  const [reason, setReason] = useState("");
  const [preview, setPreview] = useState<MergePreviewResponse | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(false);
  const [isMerging, setIsMerging] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  if (!isOpen) return null;

  const handlePreview = async () => {
    const pId = parseInt(primaryId, 10);
    const sId = parseInt(secondaryId, 10);
    if (isNaN(pId) || isNaN(sId) || pId === sId) {
      setErrorMsg("请输入有效的两个不同人物 ID");
      return;
    }

    setIsLoadingPreview(true);
    setErrorMsg(null);
    setPreview(null);
    try {
      const res = await kgApi.previewMerge(pId, sId);
      setPreview(res);
    } catch (err: any) {
      setErrorMsg(err.message || "合并预检失败");
    } finally {
      setIsLoadingPreview(false);
    }
  };

  const handleExecuteMerge = async () => {
    if (!preview) return;
    setIsMerging(true);
    setErrorMsg(null);
    try {
      await kgApi.executeMerge({
        primary_person_id: preview.primary_person_id,
        secondary_person_id: preview.secondary_person_id,
        operator,
        reason: reason || "人工审核确认两实体实为同一历史人物进行合并",
      });
      setSuccessMsg(`实体合并成功！已生成审计快照，支持随时可逆撤销恢复。`);
      onMerged && onMerged();
      setTimeout(() => {
        onClose();
      }, 1500);
    } catch (err: any) {
      setErrorMsg(err.message || "执行合并失败");
    } finally {
      setIsMerging(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm animate-in fade-in duration-150">
      <div className="relative flex max-h-[85vh] w-full max-w-xl flex-col rounded-3xl border border-white/80 bg-white shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-100 p-5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">
              <GitMerge className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-900">历史人物实体合并工具</h3>
              <p className="text-xs text-slate-500">将同一历史人物的重复/异构实体进行原子化合并</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {errorMsg && (
            <div className="flex items-center gap-2 rounded-xl bg-rose-50 border border-rose-200 p-3 text-xs text-rose-800">
              <AlertTriangle className="h-4 w-4 text-rose-600 shrink-0" />
              <span>{errorMsg}</span>
            </div>
          )}

          {successMsg && (
            <div className="flex items-center gap-2 rounded-xl bg-emerald-50 border border-emerald-200 p-3 text-xs text-emerald-800">
              <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />
              <span>{successMsg}</span>
            </div>
          )}

          {/* 输入表单 */}
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-semibold text-slate-700">主实体 ID (保留)</label>
              <input
                type="number"
                placeholder="例如: 1"
                value={primaryId}
                onChange={(e) => setPrimaryId(e.target.value)}
                className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs outline-none focus:border-primary"
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-700">次实体 ID (合并并标记归档)</label>
              <input
                type="number"
                placeholder="例如: 2"
                value={secondaryId}
                onChange={(e) => setSecondaryId(e.target.value)}
                className="mt-1 w-full rounded-xl border border-slate-200 px-3 py-2 text-xs outline-none focus:border-primary"
              />
            </div>
          </div>

          <div className="flex justify-end">
            <button
              type="button"
              disabled={isLoadingPreview || !primaryId || !secondaryId}
              onClick={handlePreview}
              className="flex items-center gap-1.5 rounded-xl bg-slate-900 px-4 py-2 text-xs font-medium text-white shadow-sm hover:bg-slate-800 disabled:opacity-50"
            >
              <span>预检合并影响与冲突</span>
              <ArrowRight className="h-3.5 w-3.5" />
            </button>
          </div>

          {/* 预检报告 */}
          {preview && (
            <div className="flex flex-col gap-3 rounded-2xl border border-slate-200/80 bg-slate-50/70 p-4 text-xs">
              <div className="flex items-center justify-between font-semibold text-slate-800">
                <span>合并影响预检摘要:</span>
                <span
                  className={`rounded-full px-2.5 py-0.5 text-[10px] font-bold ${
                    preview.safe_to_merge ? "bg-emerald-100 text-emerald-800" : "bg-amber-100 text-amber-800"
                  }`}
                >
                  {preview.safe_to_merge ? "安全可合并" : "存在潜在属性冲突"}
                </span>
              </div>

              <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-600">
                <div className="rounded-xl bg-white p-2.5 border border-slate-100">
                  迁移提及数量: <strong>{preview.mentions_to_move_count}</strong> 个
                </div>
                <div className="rounded-xl bg-white p-2.5 border border-slate-100">
                  迁移关系数量: <strong>{preview.out_relationships_to_move_count + preview.in_relationships_to_move_count}</strong> 条
                </div>
              </div>

              {preview.has_conflicts && preview.conflicts.length > 0 && (
                <div className="rounded-xl bg-amber-50 border border-amber-200 p-3 text-[11px] text-amber-900">
                  <div className="font-semibold mb-1">冲突字段提醒：</div>
                  <ul className="list-disc pl-4 space-y-0.5">
                    {preview.conflicts.map((c, i) => (
                      <li key={i}>
                        {c.field}: 主实体为 “{String(c.primary_value)}”，次实体为 “{String(c.secondary_value)}” ({c.recommendation})
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="grid grid-cols-2 gap-2 pt-2">
                <div>
                  <label className="text-[11px] text-slate-500 font-medium">操作人</label>
                  <input
                    type="text"
                    value={operator}
                    onChange={(e) => setOperator(e.target.value)}
                    className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-800 outline-none focus:border-primary"
                  />
                </div>
                <div>
                  <label className="text-[11px] text-slate-500 font-medium">合并考证理由</label>
                  <input
                    type="text"
                    placeholder="例如: 经考证为同一十三行总商"
                    value={reason}
                    onChange={(e) => setReason(e.target.value)}
                    className="mt-1 w-full rounded-xl border border-slate-200 bg-white px-2.5 py-1.5 text-xs text-slate-800 outline-none focus:border-primary"
                  />
                </div>
              </div>

              <div className="pt-2">
                <button
                  type="button"
                  disabled={isMerging}
                  onClick={handleExecuteMerge}
                  className="w-full rounded-xl bg-indigo-600 py-2.5 text-xs font-semibold text-white shadow-md shadow-indigo-600/20 hover:bg-indigo-700 disabled:opacity-50"
                >
                  {isMerging ? "正在执行原子化合并..." : "确认执行合并 (生成审计快照)"}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
