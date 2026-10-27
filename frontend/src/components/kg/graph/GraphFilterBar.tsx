"use client";

import React from "react";
import { AlertCircle, Calendar, Check, Filter, Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import { SNAGraphFilterParams } from "@/types/kg";
import { FacetsLike, buildRelationTypeOptions, yearFilterState } from "@/lib/graphScope";

interface GraphFilterBarProps {
  filters: SNAGraphFilterParams;
  onChange: (newFilters: SNAGraphFilterParams) => void;
  onRefresh?: () => void;
  isLoading?: boolean;
  /** Server-provided options; when absent the bar must not invent any. */
  facets?: FacetsLike | null;
  facetsLoading?: boolean;
  facetsError?: string | null;
}

export function GraphFilterBar({
  filters,
  onChange,
  onRefresh,
  isLoading,
  facets,
  facetsLoading,
  facetsError,
}: GraphFilterBarProps) {
  const typeOptions = buildRelationTypeOptions(facets, filters.relation_types);
  const yearState = yearFilterState(facets);

  const toggleRelationType = (type: string) => {
    const current = (filters.relation_types || []) as string[];
    const exists = current.includes(type);
    const updated = exists ? current.filter((t) => t !== type) : [...current, type];
    onChange({
      ...filters,
      relation_types:
        updated.length > 0
          ? (updated as SNAGraphFilterParams["relation_types"])
          : undefined,
    });
  };

  return (
    <div className="flex flex-col gap-3 rounded-2xl border border-white/60 bg-white/80 p-3 shadow-lg backdrop-blur-xl">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-1.5 text-xs font-semibold text-slate-700">
            <Filter className="h-3.5 w-3.5 text-primary" />
            <span>关系筛选:</span>
          </div>

          {facetsLoading && (
            <span
              className="flex items-center gap-1 text-[11px] text-slate-500"
              data-facets-loading="true"
            >
              <Loader2 className="h-3 w-3 animate-spin" />
              正在读取本组可用筛选项…
            </span>
          )}

          {!facetsLoading && facetsError && (
            <span
              className="flex items-center gap-1 text-[11px] text-rose-700"
              data-facets-error="true"
            >
              <AlertCircle className="h-3 w-3" />
              筛选项加载失败：{facetsError}（已清除上一组选项）
            </span>
          )}

          {!facetsLoading && !facetsError && typeOptions.length === 0 && (
            <span className="text-[11px] text-slate-500" data-facets-empty="true">
              该组当前视图下没有任何关系，因此没有可用的关系类型筛选项。
            </span>
          )}

          {!facetsError && typeOptions.length > 0 && (
            <div className="flex flex-wrap gap-1.5" data-relation-type-options="true">
              {typeOptions.map((opt) => (
                <button
                  key={opt.value}
                  type="button"
                  disabled={opt.disabled && !opt.selected}
                  onClick={() => toggleRelationType(opt.value)}
                  title={opt.disabled ? "本组当前视图中没有该类型的关系" : undefined}
                  data-relation-type-option={opt.value}
                  data-relation-type-count={opt.count}
                  className={`flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium transition-all disabled:cursor-not-allowed disabled:opacity-40 ${
                    opt.selected
                      ? "bg-slate-900 text-white shadow-sm"
                      : "border border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  {opt.selected && <Check className="h-3 w-3" />}
                  <span>{opt.label}</span>
                  <span className={opt.selected ? "text-slate-300" : "text-slate-400"}>
                    {opt.count}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        {onRefresh && (
          <button
            type="button"
            disabled={isLoading}
            onClick={onRefresh}
            className="flex items-center gap-1 rounded-xl bg-slate-900 px-3 py-1.5 text-xs font-medium text-white shadow-sm transition-all hover:bg-slate-800 disabled:opacity-50"
          >
            <RefreshCw className={`h-3 w-3 ${isLoading ? "animate-spin" : ""}`} />
            {isLoading ? "计算中..." : "重新计算网络"}
          </button>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-4 text-xs text-slate-600">
        {/* 时间窗口：仅按关系的日期生成选项 */}
        <div className="flex flex-wrap items-center gap-1.5">
          <Calendar className="h-3.5 w-3.5 text-slate-400" />
          <span className="text-slate-500">关系年份:</span>
          <select
            value={
              filters.period_start && filters.period_end
                ? `${filters.period_start}_${filters.period_end}`
                : "all"
            }
            disabled={!yearState.available}
            onChange={(event) => {
              const value = event.target.value;
              if (value === "all") {
                onChange({ ...filters, period_start: undefined, period_end: undefined });
                return;
              }
              const year = Number(value);
              onChange({
                ...filters,
                period_start: `${year}-01-01`,
                period_end: `${year}-12-31`,
              });
            }}
            data-year-filter-disabled={yearState.available ? "false" : "true"}
            className="rounded-lg border border-slate-200 bg-white px-2 py-1 text-xs text-slate-700 outline-none focus:border-primary disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-400"
          >
            <option value="all">
              {yearState.available ? "全部年份（按本组关系日期）" : "不可用"}
            </option>
            {yearState.years.map((year) => (
              <option key={year.year} value={String(year.year)}>
                {year.year} 年（{year.count} 条）
              </option>
            ))}
          </select>
          {!yearState.available && yearState.reason && (
            <span className="text-[11px] text-amber-700" data-year-filter-reason="true">
              {yearState.reason}
            </span>
          )}
        </div>

        {/* 未标日期关系：说明其对筛选的实际影响 */}
        <label className="flex cursor-pointer items-center gap-1.5 select-none">
          <input
            type="checkbox"
            checked={filters.include_unknown_dates ?? true}
            onChange={(event) =>
              onChange({ ...filters, include_unknown_dates: event.target.checked })
            }
            className="rounded border-slate-300 text-primary focus:ring-primary"
            data-include-unknown-dates="true"
          />
          <span title="未标日期的关系不受年份筛选影响">
            包含未标日期的关系（会影响年份筛选）
          </span>
        </label>
        {yearState.undatedHint && (
          <span className="text-[11px] text-slate-500" data-undated-hint="true">
            {yearState.undatedHint}
          </span>
        )}

        {/* 孤立点开关 */}
        <label className="flex cursor-pointer items-center gap-1.5 select-none">
          <input
            type="checkbox"
            checked={filters.include_isolates ?? true}
            onChange={(event) =>
              onChange({ ...filters, include_isolates: event.target.checked })
            }
            className="rounded border-slate-300 text-primary focus:ring-primary"
          />
          <span>包含孤立节点</span>
        </label>

        {/* 待定实体开关 */}
        <label className="flex cursor-pointer items-center gap-1.5 select-none">
          <input
            type="checkbox"
            checked={filters.include_provisional ?? false}
            onChange={(event) =>
              onChange({ ...filters, include_provisional: event.target.checked })
            }
            className="rounded border-slate-300 text-primary focus:ring-primary"
          />
          <span className="flex items-center gap-1">
            <ShieldCheck className="h-3 w-3 text-amber-500" />
            包含未证实待定实体
          </span>
        </label>
      </div>

      {facets?.mention_years && facets.mention_years.length > 0 && (
        <p className="text-[10px] text-slate-400" data-mention-years="true">
          人物提及年份（不是关系成立年份）：
          {facets.mention_years
            .slice(0, 8)
            .map((item) => `${item.year}（${item.count}）`)
            .join(" · ")}
          {facets.mention_year_note ? ` — ${facets.mention_year_note}` : ""}
        </p>
      )}
    </div>
  );
}
