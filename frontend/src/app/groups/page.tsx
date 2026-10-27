"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, FolderPlus, FolderOpen, Loader2, RefreshCw } from "lucide-react";
import { groupsApi } from "@/lib/groupsApi";
import { useHydrated } from "@/lib/useHydrated";
import { EMPTY_GROUP_NAME_MESSAGE, blockedSubmitMessage } from "@/lib/formReadiness";
import { ResearchGroup } from "@/types/group";

export default function GroupsPage() {
  const hydrated = useHydrated();
  const [groups, setGroups] = useState<ResearchGroup[]>([]);
  const [hasLoaded, setHasLoaded] = useState(false);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [isCreating, setIsCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsRefreshing(true);
    setError(null);
    try {
      const data = await groupsApi.list(1, 100);
      setGroups(data.items);
    } catch (err: any) {
      setError(err?.message || "无法加载文档组列表。");
    } finally {
      setIsRefreshing(false);
      setHasLoaded(true);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    const blocked = blockedSubmitMessage({
      value: name,
      emptyMessage: EMPTY_GROUP_NAME_MESSAGE,
      busy: isCreating,
      hydrated,
    });
    if (blocked) {
      setCreateError(blocked);
      return;
    }
    setIsCreating(true);
    setCreateError(null);
    try {
      const created = await groupsApi.create(name.trim(), description.trim() || undefined);
      setGroups((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      setName("");
      setDescription("");
      await load();
    } catch (err: any) {
      setCreateError(err?.message || "创建文档组失败。");
    } finally {
      setIsCreating(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-4 md:p-8">
      <div className="max-w-6xl mx-auto space-y-6">
        <header className="flex items-center justify-between gap-3 flex-wrap border-b border-slate-900 pb-3">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-300 flex items-center justify-center">
              <FolderOpen className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-lg font-semibold">文档组（研究范围）</h1>
              <p className="text-xs text-slate-400">
                把档案卷宗编成组，限定 RAG / Agent 的研究范围；组外文档不会混入证据。
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={load}
            disabled={isRefreshing || !hydrated}
            data-refresh-groups="true"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-xs disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isRefreshing ? "animate-spin" : ""}`} />
            {isRefreshing && hasLoaded ? "刷新中…" : "刷新"}
          </button>
        </header>

        <form
          onSubmit={handleCreate}
          data-form-ready={hydrated ? "true" : "false"}
          className="bg-slate-900/60 border border-slate-800 rounded-2xl p-4 space-y-3"
        >
          <div className="flex items-center gap-2 text-sm text-slate-200">
            <FolderPlus className="w-4 h-4 text-indigo-400" />
            新建文档组
          </div>
          <fieldset disabled={!hydrated} className="space-y-3 border-0 p-0 m-0 min-w-0">
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
              <input
                aria-label="文档组名称"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="名称，例如 Canton Consultations 1792–1793"
                className="rounded-lg bg-slate-800 border border-slate-700 text-xs px-3 py-2"
              />
              <input
                aria-label="文档组描述"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder="描述（可选）"
                className="md:col-span-2 rounded-lg bg-slate-800 border border-slate-700 text-xs px-3 py-2"
              />
            </div>
            {createError && (
              <p className="text-xs text-rose-300 flex items-center gap-1.5" data-create-error="true">
                <AlertCircle className="w-3.5 h-3.5" />
                {createError}
              </p>
            )}
            <button
              type="submit"
              disabled={!hydrated || isCreating}
              className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-medium disabled:opacity-50"
            >
              {!hydrated ? "准备中…" : isCreating ? "创建中…" : "创建文档组"}
            </button>
            {!hydrated && (
              <p className="text-xs text-slate-500" data-form-hint="true">
                正在初始化页面，稍后即可创建。
              </p>
            )}
          </fieldset>
        </form>

        {!hasLoaded && (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            正在加载文档组…
          </div>
        )}

        {hasLoaded && error && (
          <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs">
            {error}
            <button type="button" onClick={load} className="ml-2 underline">
              重试
            </button>
          </div>
        )}

        {hasLoaded && !error && groups.length === 0 && (
          <p className="text-xs text-slate-500">还没有文档组。创建第一个组，然后导入档案。</p>
        )}

        {!error && groups.length > 0 && (
          <ul className="space-y-2" data-group-list="true">
            {groups.map((group) => (
              <li
                key={group.id}
                data-group-name={group.name}
                className="bg-slate-900/60 border border-slate-800 rounded-xl p-4 flex flex-wrap items-center justify-between gap-3"
              >
                <div className="space-y-1">
                  <Link
                    href={`/groups/${group.id}`}
                    className="text-sm font-medium text-indigo-200 hover:text-indigo-100"
                  >
                    {group.name}
                  </Link>
                  <p className="text-[11px] text-slate-400">
                    {group.description || "（无描述）"}
                  </p>
                  <p className="text-[11px] text-slate-400">
                    文档 {group.document_count} 篇 · 可检索 {group.ready_count} 篇
                    {group.failed_count > 0 && (
                      <span className="text-rose-300"> · 失败 {group.failed_count} 篇</span>
                    )}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Link
                    href={`/groups/${group.id}`}
                    className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-[11px]"
                  >
                    管理
                  </Link>
                  <Link
                    href={`/rag?group=${group.id}`}
                    className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-[11px]"
                  >
                    智能问答
                  </Link>
                  <Link
                    href={`/agent?group=${group.id}`}
                    className="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-[11px]"
                  >
                    AI 研究员
                  </Link>
                </div>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
