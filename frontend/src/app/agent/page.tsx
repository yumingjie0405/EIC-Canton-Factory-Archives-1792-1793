"use client";

import React, { useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { Sparkles, Bot, AlertCircle, Bookmark, Layers, GitFork, BookOpen, ShieldCheck } from "lucide-react";
import { AgentResearchSearchBar } from "@/components/agent/AgentResearchSearchBar";
import { AgentBudgetBar } from "@/components/agent/AgentBudgetBar";
import { AgentCategorizedAnswerView } from "@/components/agent/AgentCategorizedAnswerView";
import { AgentTrajectoryView } from "@/components/agent/AgentTrajectoryView";
import { AgentPlanPreviewModal } from "@/components/agent/AgentPlanPreviewModal";
import { SourceCardList } from "@/components/rag/SourceCardList";
import { useCitationNavigation } from "@/lib/useCitationNavigation";
import { RelatedEntitiesPanel } from "@/components/rag/RelatedEntitiesPanel";
import { GenerationStatusBanner } from "@/components/ui/generation-status";
import { ResearchHistoryPanel } from "@/components/research/ResearchHistoryPanel";
import { GroupSelector } from "@/components/groups/GroupSelector";
import { parseGroupIdParam } from "@/lib/groupScope";
import { runAgentResearch, previewAgentPlan } from "@/lib/agentApi";
import { AgentPlanResponse, AgentResearchResponse } from "@/types/agent";

function AgentContent() {
  const searchParams = useSearchParams();
  const initialQuery = searchParams.get("q") || "";

  const [isLoading, setIsLoading] = useState(false);
  const [isPlanning, setIsPlanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [response, setResponse] = useState<AgentResearchResponse | null>(null);
  const [planPreview, setPlanPreview] = useState<AgentPlanResponse | null>(null);
  const [isPlanModalOpen, setIsPlanModalOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<"findings" | "trajectory" | "sources" | "kg">("findings");
  const [historyRefreshKey, setHistoryRefreshKey] = useState(0);
  const [groupId, setGroupId] = useState<number | null>(
    parseGroupIdParam(searchParams.get("group"))
  );
  const [groupsRefreshKey] = useState(0);
  // Each Agent run gets its own citation scope so ids never collide with the
  // RAG page, the history panel, or a previous run.
  const agentScope = response ? `agent-${response.task_id}` : "agent-current";
  const { activeIndex, activate, reset } = useCitationNavigation(
    agentScope,
    response?.sources.length ?? 0
  );

  // 保存最新的表单参数以便在 Plan 确认后直接执行
  const [currentParams, setCurrentParams] = useState<{
    question: string;
    maxSteps: number;
    maxToolCalls: number;
    timeFilter?: number | null;
  }>({
    question: initialQuery,
    maxSteps: 4,
    maxToolCalls: 6,
    timeFilter: null,
  });

  const handleStartResearch = async (params: {
    question: string;
    maxSteps: number;
    maxToolCalls: number;
    timeFilter?: number | null;
  }) => {
    setCurrentParams(params);
    setIsLoading(true);
    setError(null);
    reset();

    try {
      const res = await runAgentResearch({
        question: params.question,
        max_steps: params.maxSteps,
        max_tool_calls: params.maxToolCalls,
        time_filter: params.timeFilter,
        temperature: 0.0,
        include_trajectory: true,
        ...(groupId != null ? { group_id: groupId } : {}),
      });
      setResponse(res);
      setActiveTab("findings");
      setHistoryRefreshKey((key) => key + 1);
    } catch (err: any) {
      console.error("Agent research execution failed:", err);
      setError(err?.message || "Agent 自主考据执行异常，请检查网络或后端服务。");
    } finally {
      setIsLoading(false);
    }
  };

  const handlePreviewPlan = async (question: string, timeFilter?: number | null) => {
    setIsPlanning(true);
    setError(null);

    try {
      const plan = await previewAgentPlan(question, timeFilter);
      setPlanPreview(plan);
      setIsPlanModalOpen(true);
    } catch (err: any) {
      console.error("Agent plan preview failed:", err);
      setError(err?.message || "生成研究计划分解失败，请重试。");
    } finally {
      setIsPlanning(false);
    }
  };

  const handleCitationActivate = (index: number) => {
    // The evidence cards live in the "sources" tab; switch first, then navigate.
    setActiveTab("sources");
    activate(index);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 p-4 md:p-8">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* 顶部标题区 */}
        <div className="flex items-center justify-between gap-4 flex-wrap pb-2 border-b border-slate-900">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center text-white shadow-lg shadow-indigo-500/20">
              <Bot className="w-5 h-5" />
            </div>
            <div>
              <h1 className="text-xl font-bold tracking-tight text-slate-100 flex items-center gap-2">
                <span>AI 历史研究员 (Autonomous Historical Agent)</span>
                <span className="text-xs px-2 py-0.5 rounded-full bg-indigo-500/10 text-indigo-300 border border-indigo-500/30 font-normal">
                  Stage 7
                </span>
              </h1>
              <p className="text-xs text-slate-400">
                具备任务动态拆解、只读白名单工具调用、多轮迭代考据与四级防幻觉分层综述能力
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span className="flex items-center gap-1 px-2.5 py-1 rounded-full bg-slate-900 border border-slate-800">
              <ShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
              <span>知识图谱写保护</span>
            </span>
            <span className="flex items-center gap-1 px-2.5 py-1 rounded-full bg-slate-900 border border-slate-800">
              <Sparkles className="w-3.5 h-3.5 text-purple-400" />
              <span>零幻觉门控</span>
            </span>
          </div>
        </div>

        {/* 研究范围（文档组）：服务端校验并注入，模型无法扩大或缩小范围 */}
        <GroupSelector
          value={groupId}
          onChange={setGroupId}
          disabled={isLoading || isPlanning}
          refreshKey={groupsRefreshKey}
        />

        {/* 研究搜索与配置控制栏 */}
        <AgentResearchSearchBar
          onResearch={handleStartResearch}
          onPreviewPlan={handlePreviewPlan}
          isLoading={isLoading}
          isPlanning={isPlanning}
        />

        {/* 错误提示横幅 */}
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
              ? `本次研究范围：${response.group_name}`
              : "本次研究范围：全库（未限定文档组）"}
          </p>
        )}

        {/* 生成来源与引用校验状态（模型不可用时必须显式可见） */}
        <GenerationStatusBanner generation={response?.generation} />

        {/* 研究记录持久化结果（保存失败必须可见，不得伪装成功） */}
        {response?.history && !response.history.saved && (
          <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/25 text-amber-200 text-xs flex items-start gap-2">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>
              本次研究结果未保存到研究历史{response.history.error ? `：${response.history.error}` : "。"}
            </span>
          </div>
        )}

        {/* 加载骨架屏 */}
        {isLoading && (
          <div className="w-full bg-slate-900/60 border border-slate-800 rounded-2xl p-8 space-y-5 animate-pulse">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-xl bg-indigo-500/20" />
              <div className="space-y-2 flex-1">
                <div className="h-4 bg-slate-800 rounded w-1/3" />
                <div className="h-3 bg-slate-800 rounded w-1/2" />
              </div>
            </div>
            <div className="space-y-2 pt-2">
              <div className="h-3.5 bg-slate-800 rounded w-full" />
              <div className="h-3.5 bg-slate-800 rounded w-4/5" />
              <div className="h-3.5 bg-slate-800 rounded w-3/5" />
            </div>
            <div className="pt-4 flex gap-3">
              <div className="h-8 bg-slate-800 rounded-lg w-28" />
              <div className="h-8 bg-slate-800 rounded-lg w-28" />
            </div>
          </div>
        )}

        {/* 考据结果主视图 */}
        {response && !isLoading && (
          <div className="space-y-5">
            {/* 资源消耗与审计概览栏 */}
            <AgentBudgetBar
              taskId={response.task_id}
              evidenceSufficient={response.evidence_sufficient}
              budgetUsed={response.budget_used}
              warnings={response.warnings}
            />

            {/* 视图切换 Tab 栏 */}
            <div className="flex items-center gap-2 border-b border-slate-800 pb-2">
              {[
                {
                  id: "findings",
                  label: "四层学术结论",
                  icon: Sparkles,
                  badge:
                    response.categorization?.archival_facts?.length +
                    response.categorization?.kg_relationships?.length,
                },
                {
                  id: "trajectory",
                  label: "Agent 思考与工具轨迹",
                  icon: GitFork,
                  badge: response.trajectory?.length,
                },
                {
                  id: "sources",
                  label: "四级真实文献证据链",
                  icon: BookOpen,
                  badge: response.sources?.length,
                },
                {
                  id: "kg",
                  label: "关联人物与网络",
                  icon: Layers,
                  badge: response.entities?.length,
                },
              ].map((tab) => {
                const Icon = tab.icon;
                const isActive = activeTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setActiveTab(tab.id as any)}
                    className={`flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-medium transition-all ${
                      isActive
                        ? "bg-indigo-600 text-white shadow-lg shadow-indigo-600/30"
                        : "bg-slate-900/60 text-slate-400 hover:bg-slate-800 hover:text-slate-200 border border-slate-800"
                    }`}
                  >
                    <Icon className="w-3.5 h-3.5" />
                    <span>{tab.label}</span>
                    {typeof tab.badge === "number" && (
                      <span
                        className={`text-[10px] px-1.5 py-0.2 rounded-full font-mono ${
                          isActive
                            ? "bg-white/20 text-white"
                            : "bg-slate-800 text-slate-400"
                        }`}
                      >
                        {tab.badge}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>

            {/* Tab 1: 四层分层结论 */}
            {activeTab === "findings" && (
              <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
                <div className="lg:col-span-8 space-y-6">
                  <AgentCategorizedAnswerView
                    finalAnswer={response.final_answer}
                    categorization={response.categorization}
                    sourceCount={response.sources.length}
                    onCitationActivate={handleCitationActivate}
                  />
                </div>
                <div className="lg:col-span-4 space-y-6">
                  <RelatedEntitiesPanel
                    entities={response.entities}
                    relationships={response.relationships}
                  />
                </div>
              </div>
            )}

            {/* Tab 2: Agent 思考推理与调用轨迹 */}
            {activeTab === "trajectory" && (
              <AgentTrajectoryView trajectory={response.trajectory} />
            )}

            {/* Tab 3: 四级穿透文献证据链 */}
            {activeTab === "sources" && (
              <div className="max-w-4xl">
                <SourceCardList
                  sources={response.sources}
                  scope={agentScope}
                  highlightedIndex={activeIndex}
                />
              </div>
            )}

            {/* Tab 4: 关联实体档案与知识图谱关系 */}
            {activeTab === "kg" && (
              <div className="max-w-4xl">
                <RelatedEntitiesPanel
                  entities={response.entities}
                  relationships={response.relationships}
                />
              </div>
            )}
          </div>
        )}

        {/* 初始引导页卡片 (未开始考据前) */}
        {!response && !isLoading && !error && (
          <div className="bg-slate-900/40 border border-slate-850 rounded-2xl p-10 text-center space-y-6">
            <div className="w-14 h-14 rounded-3xl bg-gradient-to-tr from-indigo-500/20 to-purple-500/20 border border-indigo-500/30 text-indigo-400 flex items-center justify-center mx-auto shadow-inner">
              <Bot className="w-7 h-7" />
            </div>

            <div className="max-w-lg mx-auto space-y-2">
              <h2 className="text-base font-semibold text-slate-200">
                AI Agent 复杂历史问题多步自主考据平台
              </h2>
              <p className="text-xs text-slate-400 leading-relaxed">
                面向 1792–1793 年东印度公司与广州十三行档案，由大模型驱动智能研究助理，自动完成目标规划、图谱检索、文献穿透溯源与事实防幻觉校验。
              </p>
            </div>

            {/* 核心特性胶囊 */}
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4 max-w-3xl mx-auto text-left pt-2">
              <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80 space-y-1.5">
                <div className="text-xs font-semibold text-indigo-300 flex items-center gap-1.5">
                  <GitFork className="w-4 h-4 text-indigo-400" />
                  <span>动态任务规划</span>
                </div>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  将宏大模糊的历史问题自动拆解为有序、可核查的子研究任务，逐步迭代执行。
                </p>
              </div>

              <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80 space-y-1.5">
                <div className="text-xs font-semibold text-purple-300 flex items-center gap-1.5">
                  <ShieldCheck className="w-4 h-4 text-purple-400" />
                  <span>只读白名单工具</span>
                </div>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  严格限制工具权限，严禁将未经验证的模型推断回写至高可信度生产知识图谱。
                </p>
              </div>

              <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80 space-y-1.5">
                <div className="text-xs font-semibold text-emerald-300 flex items-center gap-1.5">
                  <Bookmark className="w-4 h-4 text-emerald-400" />
                  <span>四级穿透溯源</span>
                </div>
                <p className="text-[11px] text-slate-400 leading-relaxed">
                  所有事实主张均需直接对应档案原件 Block 级标注，无证据问题坚决主动拒答。
                </p>
              </div>
            </div>
          </div>
        )}

        {/* 研究历史：真实请求后端 /api/research/history，点击可回看保存时的回答与证据快照 */}
        <ResearchHistoryPanel refreshKey={historyRefreshKey} mode="agent" />

        {/* 计划预览弹窗 */}
        <AgentPlanPreviewModal
          plan={planPreview}
          isOpen={isPlanModalOpen}
          onClose={() => setIsPlanModalOpen(false)}
          onExecute={() => {
            if (planPreview) {
              handleStartResearch({
                question: planPreview.question,
                maxSteps: 4,
                maxToolCalls: 6,
                timeFilter: planPreview.time_filter,
              });
            }
          }}
          isExecuting={isLoading}
        />
      </div>
    </div>
  );
}

export default function AgentPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-slate-950 p-8 text-slate-400">
          正在初始化 AI Agent 研究平台...
        </div>
      }
    >
      <AgentContent />
    </Suspense>
  );
}
