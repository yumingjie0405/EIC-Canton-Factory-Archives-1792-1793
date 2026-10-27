"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { FolderOpen, RefreshCw } from "lucide-react";
import { groupsApi } from "@/lib/groupsApi";
import { emptyGroupWarning, isMissingScope, missingScopeMessage, scopeLabel } from "@/lib/groupScope";
import { ResearchGroup } from "@/types/group";

interface GroupSelectorProps {
  /** Selected group id, `null` means research across the whole archive. */
  value: number | null;
  onChange: (groupId: number | null) => void;
  disabled?: boolean;
  /** Reload the group list (e.g. after creating a group elsewhere). */
  refreshKey?: number;
  compact?: boolean;
}

/**
 * Picks the research scope (a document group). The chosen id is sent with the
 * research request; the server re-validates it and never lets the model widen it.
 */
export function GroupSelector({
  value,
  onChange,
  disabled,
  refreshKey = 0,
  compact = false,
}: GroupSelectorProps) {
  const [groups, setGroups] = useState<ResearchGroup[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await groupsApi.list(1, 100);
      setGroups(data.items);
    } catch (err: any) {
      setError(err?.message || "无法加载文档组列表。");
    } finally {
      setIsLoading(false);
    }
    // A requested group id is never silently dropped: if it is unknown the
    // selector reports it, because falling back to a whole-archive search would
    // answer a different question than the one the URL asked for.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);

  useEffect(() => {
    load();
  }, [load]);

  const selected = groups.find((group) => group.id === value) || null;
  const missing = isMissingScope(
    value,
    groups.map((group) => group.id),
    { isLoading, hasError: !!error }
  );
  const warning = emptyGroupWarning(selected);

  return (
    <div
      className={`w-full bg-slate-900/60 border border-slate-800 rounded-2xl ${
        compact ? "p-3" : "p-4"
      } space-y-2`}
      data-group-selector="true"
      data-selected-group={value ?? ""}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-xs text-slate-300">
          <FolderOpen className="w-3.5 h-3.5 text-indigo-400" />
          研究范围
        </span>
        <select
          aria-label="选择研究文档组"
          value={value ?? ""}
          disabled={disabled || isLoading}
          onChange={(event) => {
            const raw = event.target.value;
            onChange(raw ? Number(raw) : null);
          }}
          className="min-w-[220px] rounded-lg bg-slate-800 border border-slate-700 text-xs text-slate-100 px-2 py-1.5 disabled:opacity-50"
        >
          <option value="">全库（不限定文档组）</option>
          {missing && (
            <option value={value ?? ""}>文档组 #{value}（不存在或无权访问）</option>
          )}
          {groups.map((group) => (
            <option key={group.id} value={group.id}>
              {group.name}（{group.ready_count}/{group.document_count} 篇可检索）
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={load}
          disabled={isLoading}
          className="flex items-center gap-1 px-2 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] disabled:opacity-50"
          title="刷新文档组列表"
        >
          <RefreshCw className={`w-3 h-3 ${isLoading ? "animate-spin" : ""}`} />
          刷新
        </button>
        <Link href="/groups" className="text-[11px] text-indigo-300 hover:text-indigo-200 underline">
          管理文档组
        </Link>
        {selected && (
          <span className="text-[11px] text-slate-400" data-scope-label="true">
            {scopeLabel(selected)}
          </span>
        )}
      </div>
      {error && (
        <p className="text-[11px] text-rose-300" data-group-error="true">
          文档组列表加载失败：{error}
        </p>
      )}
      {missing && (
        <p className="text-[11px] text-rose-300" data-group-missing="true">
          {missingScopeMessage(value)}
          <button
            type="button"
            onClick={() => onChange(null)}
            className="ml-2 underline hover:text-rose-200"
          >
            改为全库检索
          </button>
        </p>
      )}
      {warning && (
        <p className="text-[11px] text-amber-200" data-group-warning="true">
          {warning}
        </p>
      )}
    </div>
  );
}

export default GroupSelector;
