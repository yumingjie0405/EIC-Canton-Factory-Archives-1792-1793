"use client";

import React from "react";
import { roleCoverageLabel, roleLegendItems } from "@/lib/graphScope";

/**
 * Role categories used for node colouring.
 *
 * Keys are the *normalised* categories produced by the backend (`role_taxonomy.py`),
 * not free-text roles: the legend and the canvas must share one vocabulary, and a
 * category with zero people in the current view must not be advertised as present.
 */
export const ROLE_COLOR_MAP: Record<
  string,
  { bg: string; border: string; text: string; label: string }
> = {
  hong_merchant: {
    bg: "bg-amber-500",
    border: "border-amber-600",
    text: "text-amber-700",
    label: "行商（保商）",
  },
  foreign_merchant: {
    bg: "bg-indigo-500",
    border: "border-indigo-600",
    text: "text-indigo-700",
    label: "外商 / 大班",
  },
  official: {
    bg: "bg-rose-500",
    border: "border-rose-600",
    text: "text-rose-700",
    label: "清廷官员",
  },
  diplomatic: {
    bg: "bg-violet-500",
    border: "border-violet-600",
    text: "text-violet-700",
    label: "外交使节",
  },
  interpreter: {
    bg: "bg-emerald-500",
    border: "border-emerald-600",
    text: "text-emerald-700",
    label: "通事 / 引水",
  },
  comprador: {
    bg: "bg-teal-500",
    border: "border-teal-600",
    text: "text-teal-700",
    label: "买办",
  },
  unknown: {
    bg: "bg-slate-400",
    border: "border-slate-500",
    text: "text-slate-600",
    label: "角色未知",
  },
};

const CATEGORY_KEYS = Object.keys(ROLE_COLOR_MAP);

/**
 * Resolve a role to a colour category.
 *
 * The backend already sends the normalised category, which is returned as-is. Free-text
 * roles are still accepted for older payloads: those fall back to keyword matching, and
 * anything unrecognised becomes ``unknown`` — never "other", because "no role recorded"
 * and "a different role" are different claims.
 */
export function getRoleKey(role?: string | null): string {
  if (!role) return "unknown";
  const value = role.trim().toLowerCase();
  if (!value) return "unknown";
  if (CATEGORY_KEYS.includes(value)) return value;

  if (
    value.includes("行商") ||
    value.includes("保商") ||
    value.includes("security") ||
    value.includes("hong merchant") ||
    value.includes("cohong")
  ) {
    return "hong_merchant";
  }
  if (
    value.includes("大班") ||
    value.includes("supercargo") ||
    value.includes("supra cargo") ||
    value.includes("captain") ||
    value.includes("select committee")
  ) {
    return "foreign_merchant";
  }
  if (
    value.includes("官") ||
    value.includes("mandarin") ||
    value.includes("viceroy") ||
    value.includes("hoppo") ||
    value.includes("governor") ||
    value.includes("海关") ||
    value.includes("总督")
  ) {
    return "official";
  }
  if (
    value.includes("ambassador") ||
    value.includes("envoy") ||
    value.includes("plenipotentiary") ||
    value.includes("使节") ||
    value.includes("公使")
  ) {
    return "diplomatic";
  }
  if (
    value.includes("通事") ||
    value.includes("引水") ||
    value.includes("linguist") ||
    value.includes("interpreter") ||
    value.includes("translator")
  ) {
    return "interpreter";
  }
  if (value.includes("comprador") || value.includes("买办")) {
    return "comprador";
  }
  return "unknown";
}

export interface RoleCounts {
  [category: string]: number;
}

export interface GraphLegendProps {
  /** Person count per category **in the current view**; only these are listed. */
  roleCounts?: RoleCounts | null;
  totalPersons?: number | null;
  knownRoleCount?: number | null;
  unknownRoleCount?: number | null;
}

export function GraphLegend({
  roleCounts,
  totalPersons,
  knownRoleCount,
  unknownRoleCount,
}: GraphLegendProps = {}) {
  // With counts, list exactly the categories that occur (computed by a tested pure
  // helper). Without counts, keep the full palette as a pure colour key.
  const items = roleCounts ? roleLegendItems(roleCounts) : null;
  const entries = items ? items.map((item) => item.key) : CATEGORY_KEYS;
  const countFor = (key: string): number | undefined =>
    items ? items.find((item) => item.key === key)?.count : undefined;

  return (
    <div className="flex flex-col gap-2 rounded-2xl border border-white/60 bg-white/80 p-3 text-xs shadow-lg backdrop-blur-xl">
      <div className="font-semibold text-slate-800">图例说明</div>

      {roleCounts && (
        <div className="text-[10px] text-slate-500" data-role-coverage="true">
          人物 {totalPersons ?? "—"} 人 · 已知角色 {knownRoleCount ?? 0} 人 · 角色未知{" "}
          {unknownRoleCount ?? 0} 人
        </div>
      )}

      <div className="flex flex-col gap-1.5">
        {entries.map((key) => {
          const item = ROLE_COLOR_MAP[key];
          const count = countFor(key);
          return (
            <div key={key} className="flex items-center gap-2" data-legend-role={key}>
              <span className={`h-2.5 w-2.5 rounded-full ${item.bg}`} />
              <span className="text-slate-600">{item.label}</span>
              {typeof count === "number" && (
                <span className="text-[10px] text-slate-400">{count}</span>
              )}
            </div>
          );
        })}
        {roleCounts && entries.length === 0 && (
          <div className="text-[10px] text-slate-500">当前视图没有人物节点。</div>
        )}
      </div>

      <div className="my-1 border-t border-slate-100" />
      <div className="flex flex-col gap-1 text-[11px] text-slate-500">
        <div className="flex items-center gap-2">
          <span className="h-0.5 w-3 bg-slate-400" />
          <span>实线: 已证实档案关系</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="h-0.5 w-3 border-b border-dashed border-slate-400" />
          <span>虚线: 待审核/未证实</span>
        </div>
      </div>
    </div>
  );
}
