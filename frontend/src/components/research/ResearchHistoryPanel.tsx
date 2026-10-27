"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  ChevronRight,
  History as HistoryIcon,
  Loader2,
  RefreshCw,
  X,
} from "lucide-react";
import { researchApi } from "@/lib/researchApi";
import {
  HISTORY_PAGE_SIZE,
  historyHasMore,
  historyProgressLabel,
  mergeHistoryItems,
  nextHistoryPage,
} from "@/lib/historyPagination";
import { ResearchRecordView } from "@/components/research/ResearchRecordView";
import {
  ResearchMode,
  ResearchRecordDetail,
  ResearchRecordSummary,
} from "@/types/research";

interface ResearchHistoryPanelProps {
  /** Bump this value to reload the list (e.g. after a new research run). */
  refreshKey?: number;
  /** Only show records produced by this pipeline. */
  mode?: ResearchMode;
  className?: string;
}

const STATUS_STYLES: Record<string, { label: string; className: string }> = {
  completed: {
    label: "已完成",
    className: "bg-emerald-500/10 text-emerald-300 border-emerald-500/30",
  },
  degraded: {
    label: "降级结果",
    className: "bg-amber-500/10 text-amber-300 border-amber-500/30",
  },
  insufficient_evidence: {
    label: "证据不足·拒答",
    className: "bg-slate-700/40 text-slate-300 border-slate-600/40",
  },
  failed: {
    label: "执行失败",
    className: "bg-rose-500/10 text-rose-300 border-rose-500/30",
  },
};

function statusStyle(status: string) {
  return (
    STATUS_STYLES[status] || {
      label: status,
      className: "bg-slate-800 text-slate-300 border-slate-700",
    }
  );
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function ResearchHistoryPanel({ refreshKey = 0, mode, className }: ResearchHistoryPanelProps) {
  const [items, setItems] = useState<ResearchRecordSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [isLoading, setIsLoading] = useState(true);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [exhausted, setExhausted] = useState(false);

  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<ResearchRecordDetail | null>(null);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  const loadList = useCallback(async () => {
    setIsLoading(true);
    setListError(null);
    setLoadMoreError(null);
    try {
      const data = await researchApi.listHistory(1, HISTORY_PAGE_SIZE, mode);
      setItems(data.items);
      setTotal(data.total);
      setPage(1);
      setExhausted(data.items.length === 0);
    } catch (err: any) {
      setListError(err?.message || "无法加载研究历史，请稍后重试。");
    } finally {
      setIsLoading(false);
    }
  }, [mode]);

  const loadMore = useCallback(async () => {
    if (isLoadingMore) return;
    const targetPage = nextHistoryPage(page);
    setIsLoadingMore(true);
    setLoadMoreError(null);
    try {
      const data = await researchApi.listHistory(targetPage, HISTORY_PAGE_SIZE, mode);
      // Merge instead of replacing: records may have shifted between pages when a
      // new run was saved, and previously loaded rows must never disappear.
      setItems((current) => mergeHistoryItems(current, data.items));
      setTotal(data.total);
      setPage(targetPage);
      if (data.items.length === 0) setExhausted(true);
    } catch (err: any) {
      setLoadMoreError(err?.message || "加载更多历史记录失败，请重试。");
    } finally {
      setIsLoadingMore(false);
    }
  }, [isLoadingMore, mode, page]);

  useEffect(() => {
    // A new research result (refreshKey) or a mode switch invalidates the pages
    // already loaded, so paging restarts from page 1 and stale detail is dropped.
    setSelectedId(null);
    setDetail(null);
    setDetailError(null);
    loadList();
  }, [loadList, refreshKey]);

  const openRecord = async (recordId: number) => {
    if (selectedId === recordId) {
      setSelectedId(null);
      setDetail(null);
      setDetailError(null);
      return;
    }
    setSelectedId(recordId);
    setDetail(null);
    setDetailError(null);
    setIsDetailLoading(true);
    try {
      const data = await researchApi.getRecord(recordId);
      setDetail(data);
    } catch (err: any) {
      setDetailError(err?.message || "无法加载该历史记录。");
    } finally {
      setIsDetailLoading(false);
    }
  };

  return (
    <section
      className={`w-full bg-slate-900/60 border border-slate-800 rounded-2xl p-5 space-y-4 ${className || ""}`}
    >
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <HistoryIcon className="w-4 h-4 text-indigo-400" />
          <h2 className="text-sm font-semibold text-slate-200">
            研究历史{mode ? `（${mode === "agent" ? "Agent" : "Graph RAG"}）` : ""}
          </h2>
          {!isLoading && !listError && (
            <span className="text-[11px] px-1.5 py-0.5 rounded-full bg-slate-800 text-slate-400 font-mono">
              {total}
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={loadList}
          disabled={isLoading}
          className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] disabled:opacity-50"
          title="重新加载历史记录"
        >
          <RefreshCw className={`w-3 h-3 ${isLoading ? "animate-spin" : ""}`} />
          刷新
        </button>
      </div>

      {isLoading && (
        <div className="flex items-center gap-2 text-xs text-slate-400 py-4">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          正在加载研究历史…
        </div>
      )}

      {!isLoading && listError && (
        <div className="flex items-start gap-2 p-3 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p>{listError}</p>
            <p className="text-rose-300/70">
              若后端尚未执行研究记录表迁移，历史功能会显示为不可用（不会伪造空历史）。
            </p>
          </div>
        </div>
      )}

      {!isLoading && !listError && items.length === 0 && (
        <p className="text-xs text-slate-500 py-3">
          暂无研究历史。执行一次研究后，问题、回答、证据快照与生成状态将自动保存于此。
        </p>
      )}

      {!isLoading && !listError && items.length > 0 && (
        <ul className="divide-y divide-slate-800">
          {items.map((item) => {
            const style = statusStyle(item.status);
            const isOpen = selectedId === item.id;
            return (
              <li key={item.id} className="py-2">
                <button
                  type="button"
                  onClick={() => openRecord(item.id)}
                  className="w-full text-left flex items-start gap-2 group"
                >
                  <ChevronRight
                    className={`w-3.5 h-3.5 mt-1 shrink-0 text-slate-500 transition-transform ${
                      isOpen ? "rotate-90 text-indigo-400" : ""
                    }`}
                  />
                  <span className="flex-1 space-y-1">
                    <span className="block text-xs text-slate-200 group-hover:text-white leading-snug">
                      {item.question}
                    </span>
                    <span className="flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
                      <span className="font-mono">#{item.id}</span>
                      <span className="px-1.5 py-0.2 rounded bg-slate-800 text-slate-400 uppercase">
                        {item.mode}
                      </span>
                      <span className={`px-1.5 py-0.2 rounded border ${style.className}`}>
                        {style.label}
                      </span>
                      {item.group_name && (
                        <span
                          className="px-1.5 py-0.2 rounded bg-indigo-500/10 text-indigo-300 border border-indigo-500/30"
                          data-history-group={item.id}
                          title={`研究范围：${item.group_name}`}
                        >
                          {item.group_name}
                        </span>
                      )}
                      {item.citation_valid === false && (
                        <span className="px-1.5 py-0.2 rounded bg-rose-500/10 text-rose-300 border border-rose-500/30">
                          引用校验未通过
                        </span>
                      )}
                      <span>{item.source_count} 条证据</span>
                      <span>{formatTime(item.created_at)}</span>
                    </span>
                  </span>
                </button>

                {isOpen && (
                  <div className="mt-3 ml-5 space-y-3">
                    {isDetailLoading && (
                      <div className="flex items-center gap-2 text-xs text-slate-400">
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        正在载入历史记录…
                      </div>
                    )}
                    {!isDetailLoading && detailError && (
                      <div className="flex items-center gap-2 text-xs text-rose-300">
                        <AlertCircle className="w-3.5 h-3.5" />
                        {detailError}
                      </div>
                    )}
                    {!isDetailLoading && detail && (
                      <div className="space-y-3">
                        <ResearchRecordView detail={detail} />
                        <button
                          type="button"
                          onClick={() => {
                            setSelectedId(null);
                            setDetail(null);
                          }}
                          className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200"
                        >
                          <X className="w-3 h-3" />
                          收起
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}

      {!isLoading && !listError && items.length > 0 && (
        <div className="space-y-2" data-history-paging="true" data-history-total={total}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[11px] text-slate-500">{historyProgressLabel(items.length, total)}</span>
            {historyHasMore(items.length, total) && !exhausted && (
              <button
                type="button"
                onClick={loadMore}
                disabled={isLoadingMore}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 text-[11px] disabled:opacity-50"
                title="加载更早的研究记录"
              >
                {isLoadingMore ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <ChevronRight className="w-3 h-3" />
                )}
                {isLoadingMore ? "正在加载…" : "加载更多"}
              </button>
            )}
          </div>
          {loadMoreError && (
            <div className="flex items-center gap-2 p-2 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs">
              <AlertCircle className="w-3.5 h-3.5 shrink-0" />
              <span className="flex-1">{loadMoreError}</span>
              <button
                type="button"
                onClick={loadMore}
                className="px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 text-slate-200"
              >
                重试
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default ResearchHistoryPanel;
