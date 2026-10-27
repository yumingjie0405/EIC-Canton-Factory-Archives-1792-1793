"use client";

import React from "react";
import { AlertTriangle, Database, FlaskConical, ShieldQuestion, UserCheck } from "lucide-react";
import { mustWarnSynthetic, sourceKindShort } from "@/types/kgWorkflow";

/**
 * One badge for the *origin* of a record.
 *
 * Deliberately separate from claim/review status: a relationship can be `accepted`
 * and still be rule-stub output, and the UI must never collapse those two facts into
 * one green tick.
 */
export function ProvenanceBadge({
  kind,
  synthetic,
  counts,
  className = "",
}: {
  kind?: string | null;
  synthetic?: boolean;
  counts?: Record<string, number> | null;
  className?: string;
}) {
  const warn = synthetic ?? mustWarnSynthetic(kind);
  const label = sourceKindShort(kind);
  const total = counts
    ? Object.values(counts).reduce((sum, value) => sum + value, 0)
    : 0;
  const Icon = warn
    ? kind === "mock_text_derived"
      ? FlaskConical
      : AlertTriangle
    : kind === "provider_reported"
    ? Database
    : kind === "historical_seed"
    ? UserCheck
    : ShieldQuestion;

  const tone = warn
    ? "border-amber-300 bg-amber-50 text-amber-800"
    : kind === "provider_reported"
    ? "border-emerald-300 bg-emerald-50 text-emerald-800"
    : "border-slate-300 bg-slate-50 text-slate-700";

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium ${tone} ${className}`}
      title={
        warn
          ? "该记录不是档案发现：只能用于流程与界面验证"
          : "来源已记录；是否成立仍取决于人工审核"
      }
      data-provenance-kind={kind ?? "unknown"}
      data-synthetic={warn ? "true" : "false"}
    >
      <Icon className="h-3 w-3" />
      {label}
      {total > 0 && <span className="text-slate-500">· {total} 条证据</span>}
    </span>
  );
}

/** Banner used at the top of every list/detail that may contain synthetic rows. */
export function SyntheticWarning({ note }: { note?: string | null }) {
  if (!note) return null;
  return (
    <div
      className="flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-[11px] text-amber-900"
      data-synthetic-warning="true"
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{note}</span>
    </div>
  );
}
