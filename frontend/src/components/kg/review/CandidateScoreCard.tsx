"use client";

import React from "react";
import { Check, X, Clock, AlertTriangle, ShieldCheck, Sparkles, UserCheck } from "lucide-react";
import { CandidateMatch } from "@/types/kg";

interface CandidateScoreCardProps {
  candidate: CandidateMatch;
  onConfirm: (personId: number) => void;
  onReject: (personId: number) => void;
  isSubmitting?: boolean;
}

export function CandidateScoreCard({
  candidate,
  onConfirm,
  onReject,
  isSubmitting,
}: CandidateScoreCardProps) {
  const percentage = Math.round(candidate.total_score * 100);

  return (
    <div
      className={`flex flex-col gap-3 rounded-2xl border p-4 transition-all ${
        candidate.fatal_conflict
          ? "border-rose-200 bg-rose-50/40"
          : candidate.total_score >= 0.75
          ? "border-emerald-200 bg-emerald-50/30 shadow-xs"
          : "border-slate-200 bg-white"
      }`}
    >
      {/* 头部信息 */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h4 className="text-sm font-bold text-slate-900">{candidate.display_name}</h4>
            {candidate.canonical_name_zh && (
              <span className="text-xs text-slate-500">{candidate.canonical_name_zh}</span>
            )}
            {candidate.canonical_name_en && (
              <span className="text-xs text-slate-400">({candidate.canonical_name_en})</span>
            )}
          </div>
          <div className="mt-0.5 text-xs text-slate-500">
            {candidate.primary_role || "未标注角色"} · {candidate.organization || "独立人物"} · 生卒: (
            {candidate.birth_year || "?"}–{candidate.death_year || "?"})
          </div>
        </div>

        {/* 综合匹配度徽章 */}
        <div className="flex flex-col items-end">
          <div
            className={`flex items-center gap-1 rounded-xl px-2.5 py-1 text-xs font-bold ${
              candidate.fatal_conflict
                ? "bg-rose-100 text-rose-700"
                : percentage >= 75
                ? "bg-emerald-100 text-emerald-800"
                : "bg-amber-100 text-amber-800"
            }`}
          >
            <Sparkles className="h-3 w-3" />
            <span>匹配度 {percentage}%</span>
          </div>
        </div>
      </div>

      {/* 致命冲突警告条 */}
      {candidate.fatal_conflict && candidate.conflict_reasons.length > 0 && (
        <div className="flex items-start gap-1.5 rounded-xl bg-rose-100/70 p-2 text-xs text-rose-800">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-rose-600" />
          <div>
            <span className="font-semibold">时空/代际致命冲突警告：</span>
            <ul className="mt-0.5 list-disc pl-4 text-[11px]">
              {candidate.conflict_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {/* 6 维可解释性评分细项条形图 */}
      <div className="grid grid-cols-3 gap-2 rounded-xl bg-slate-50/80 p-2.5 text-[11px] text-slate-600">
        <div>
          <div className="flex justify-between text-slate-500">
            <span>字面相似 (40%)</span>
            <span className="font-semibold text-slate-700">
              {Math.round(candidate.name_score * 100)}%
            </span>
          </div>
          <div className="mt-1 h-1.5 w-full rounded-full bg-slate-200">
            <div
              className="h-1.5 rounded-full bg-primary"
              style={{ width: `${candidate.name_score * 100}%` }}
            />
          </div>
        </div>

        <div>
          <div className="flex justify-between text-slate-500">
            <span>时空相容 (20%)</span>
            <span className="font-semibold text-slate-700">
              {Math.round(candidate.temporal_score * 100)}%
            </span>
          </div>
          <div className="mt-1 h-1.5 w-full rounded-full bg-slate-200">
            <div
              className={`h-1.5 rounded-full ${
                candidate.temporal_score < 0.2 ? "bg-rose-500" : "bg-emerald-500"
              }`}
              style={{ width: `${candidate.temporal_score * 100}%` }}
            />
          </div>
        </div>

        <div>
          <div className="flex justify-between text-slate-500">
            <span>角色身份 (15%)</span>
            <span className="font-semibold text-slate-700">
              {Math.round(candidate.role_score * 100)}%
            </span>
          </div>
          <div className="mt-1 h-1.5 w-full rounded-full bg-slate-200">
            <div
              className="h-1.5 rounded-full bg-indigo-500"
              style={{ width: `${candidate.role_score * 100}%` }}
            />
          </div>
        </div>

        <div>
          <div className="flex justify-between text-slate-500">
            <span>共现上下文 (10%)</span>
            <span className="font-semibold text-slate-700">
              {Math.round(candidate.context_score * 100)}%
            </span>
          </div>
          <div className="mt-1 h-1.5 w-full rounded-full bg-slate-200">
            <div
              className="h-1.5 rounded-full bg-purple-500"
              style={{ width: `${candidate.context_score * 100}%` }}
            />
          </div>
        </div>

        <div>
          <div className="flex justify-between text-slate-500">
            <span>地点相容 (10%)</span>
            <span className="font-semibold text-slate-700">
              {Math.round(candidate.spatial_score * 100)}%
            </span>
          </div>
          <div className="mt-1 h-1.5 w-full rounded-full bg-slate-200">
            <div
              className="h-1.5 rounded-full bg-sky-500"
              style={{ width: `${candidate.spatial_score * 100}%` }}
            />
          </div>
        </div>

        <div>
          <div className="flex justify-between text-slate-500">
            <span>外部权威 (5%)</span>
            <span className="font-semibold text-slate-700">
              {Math.round(candidate.authority_score * 100)}%
            </span>
          </div>
          <div className="mt-1 h-1.5 w-full rounded-full bg-slate-200">
            <div
              className="h-1.5 rounded-full bg-amber-500"
              style={{ width: `${candidate.authority_score * 100}%` }}
            />
          </div>
        </div>
      </div>

      {/* 审校操作按钮 */}
      <div className="flex items-center justify-end gap-2 pt-1">
        <button
          type="button"
          disabled={isSubmitting}
          onClick={() => onReject(candidate.person_id)}
          className="flex items-center gap-1 rounded-xl border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-50 hover:text-rose-600 transition-colors disabled:opacity-50"
        >
          <X className="h-3.5 w-3.5" />
          <span>排除该候选</span>
        </button>

        <button
          type="button"
          disabled={isSubmitting || candidate.fatal_conflict}
          onClick={() => onConfirm(candidate.person_id)}
          className={`flex items-center gap-1 rounded-xl px-3.5 py-1.5 text-xs font-medium text-white shadow-sm transition-all disabled:opacity-50 ${
            candidate.fatal_conflict
              ? "bg-slate-400 cursor-not-allowed"
              : "bg-emerald-600 hover:bg-emerald-700 shadow-emerald-600/20"
          }`}
        >
          <Check className="h-3.5 w-3.5" />
          <span>确认绑定消歧</span>
        </button>
      </div>
    </div>
  );
}
