"use client";

import React, { useState, useEffect, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { RagSearchBar } from "@/components/rag/RagSearchBar";
import { RagAnswerView } from "@/components/rag/RagAnswerView";
import { SourceCardList } from "@/components/rag/SourceCardList";
import { RelatedEntitiesPanel } from "@/components/rag/RelatedEntitiesPanel";
import { GenerationStatusBanner } from "@/components/ui/generation-status";
import { ResearchHistoryPanel } from "@/components/research/ResearchHistoryPanel";
import { GroupSelector } from "@/components/groups/GroupSelector";
import { parseGroupIdParam } from "@/lib/groupScope";
import { askGraphRag } from "@/lib/ragApi";
import { RagQueryResponse } from "@/types/rag";
import { useCitationNavigation } from "@/lib/useCitationNavigation";
import { Sparkles, History, HelpCircle, AlertCircle } from "lucide-react";

/** One result is on screen at a time on this page, so the scope is fixed. */
const RAG_SCOPE = "rag-current";

function RagContent() {
  const searchParams = useSearchParams();
  const initialQuery = searchParams.get("q") || "";

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<RagQueryResponse | null>(null);
  const [groupId, setGroupId] = useState<number | null>(
    parseGroupIdParam(searchParams.get("group"))
  );
  const [groupsRefreshKey] = useState(0);
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const { activeIndex, activate, reset } = useCitationNavigation(
    RAG_SCOPE,
    response?.sources.length ?? 0
  );

  const handleSearch = async (
    question: string,
    includeGraph: boolean = true,
    timeFilter?: number | null
  ) => {
    setIsLoading(true);
    setError(null);
    reset();

    try {
      const res = await askGraphRag({
        question,
        include_graph: includeGraph,
        time_filter: timeFilter,
        top_k: 5,
        ...(groupId != null ? { group_id: groupId } : {}),
      });
      setResponse(res);
      setHistoryRefreshKey((key) => key + 1);
    } catch (err: any) {
      console.error("RAG query failed:", err);
      setError(err?.message || "智能检索执行异常，请检查网络或后端服务。");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (initialQuery && !response) {
      handleSearch(initialQuery);
    }
  }, [initialQuery]);

  const handleCitationActivate = (index: number) => {
    activate(index);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-4 md:p-8">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* 研究范围（文档组）：服务端校验并注入，模型无法扩大范围 */}
        <GroupSelector
          value={groupId}
          onChange={setGroupId}
          disabled={isLoading}
          refreshKey={groupsRefreshKey}
        />

        {/* 搜索栏 */}
        <RagSearchBar onSearch={handleSearch} isLoading={isLoading} />

        {/* 错误警告 */}
        {error && (
          <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* 本次回答实际使用的研究范围 */}
        {response && (
          <p className="text-[11px] text-slate-400" data-answer-scope="true">
            {response.group_name
              ? `本次检索范围：${response.group_name}`
              : "本次检索范围：全库（未限定文档组）"}
          </p>
        )}

        {/* 生成来源与引用校验状态（模型不可用时必须显式可见） */}
        <GenerationStatusBanner generation={response?.generation} />

        {/* 研究记录持久化结果（保存失败必须可见，不得伪装成功） */}
        {response?.history && !response.history.saved && (
          <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/25 text-amber-200 text-xs flex items-start gap-2">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>
              本次检索结果未保存到研究历史{response.history.error ? `：${response.history.error}` : "。"}
            </span>
          </div>
        )}

        {/* 正在检索骨架屏 */}
        {isLoading && (
          <div className="w-full bg-slate-900/60 border border-slate-800 rounded-2xl p-8 space-y-4 animate-pulse">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-slate-800" />
              <div className="space-y-2 flex-1">
                <div className="h-4 bg-slate-800 rounded w-1/4" />
                <div className="h-3 bg-slate-800 rounded w-1/2" />
              </div>
            </div>
            <div className="space-y-2 pt-4">
              <div className="h-3.5 bg-slate-800 rounded w-full" />
              <div className="h-3.5 bg-slate-800 rounded w-5/6" />
              <div className="h-3.5 bg-slate-800 rounded w-4/6" />
            </div>
          </div>
        )}

        {/* 检索结果展示 */}
        {response && !isLoading && (
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
            {/* 左侧主要区域：AI 回答与档案证据溯源 */}
            <div className="lg:col-span-8 space-y-6">
              <RagAnswerView
                response={response}
                onCitationActivate={handleCitationActivate}
              />

              <SourceCardList
                sources={response.sources}
                scope={RAG_SCOPE}
                highlightedIndex={activeIndex}
              />
            </div>

            {/* 右侧边栏：图谱事实与关联实体档案 */}
            <div className="lg:col-span-4 space-y-6">
              <RelatedEntitiesPanel
                entities={response.entities}
                relationships={response.relationships}
              />
            </div>
          </div>
        )}

        {/* 研究历史：真实请求后端 /api/research/history，点击可回看保存时的回答与证据快照 */}
        <ResearchHistoryPanel refreshKey={historyRefreshKey} mode="rag" />
        {/* 未搜索时的初始引导卡片 */}
        {!response && !isLoading && !error && (
          <div className="bg-slate-900/40 border border-slate-850 rounded-2xl p-10 text-center space-y-4">
            <div className="w-12 h-12 rounded-2xl bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 flex items-center justify-center mx-auto">
              <Sparkles className="w-6 h-6" />
            </div>
            <div className="max-w-md mx-auto space-y-1">
              <h3 className="text-base font-semibold text-slate-200">
                探索 18 世纪广州十三行与东印度公司历史档案
              </h3>
              <p className="text-xs text-slate-400 leading-relaxed">
                输入自然语言问题，系统将联合查询文档知识库、向量文本库与社会关系图谱，生成带严格引注的学术级答案。
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function RagPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-slate-950 p-8 text-slate-400">正在初始化智能检索系统...</div>}>
      <RagContent />
    </Suspense>
  );
}
