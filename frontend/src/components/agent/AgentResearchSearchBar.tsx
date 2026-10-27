"use client";

import React, { useState } from "react";
import { Search, Sparkles, SlidersHorizontal, ArrowRight, Compass } from "lucide-react";
import { useHydrated } from "@/lib/useHydrated";
import {
  EMPTY_PLAN_QUESTION_MESSAGE,
  EMPTY_QUESTION_MESSAGE,
  blockedSubmitMessage,
} from "@/lib/formReadiness";

interface AgentResearchSearchBarProps {
  onResearch: (params: {
    question: string;
    maxSteps: number;
    maxToolCalls: number;
    timeFilter?: number | null;
  }) => void;
  onPreviewPlan?: (question: string, timeFilter?: number | null) => void;
  isLoading: boolean;
  isPlanning?: boolean;
}

const EXAMPLE_QUERIES = [
  {
    title: "蔡世官债务交涉与东印度公司谈判",
    query: "详细考证蔡世官在1793年的债务纠纷与东印度公司的谈判过程",
    year: 1793,
  },
  {
    title: "潘振承与蔡世官的贸易网络关系",
    query: "潘振承与蔡世官在广州十三行贸易中的商业往来与社会关系",
    year: null,
  },
  {
    title: "防伪考据：拿破仑秘密访问十三行",
    query: "考证拿破仑波拿巴是否曾于1792年秘密访问广州十三行商馆",
    year: 1792,
  },
];

export const AgentResearchSearchBar: React.FC<AgentResearchSearchBarProps> = ({
  onResearch,
  onPreviewPlan,
  isLoading,
  isPlanning = false,
}) => {
  const hydrated = useHydrated();
  const [question, setQuestion] = useState("");
  const [maxSteps, setMaxSteps] = useState(4);
  const [maxToolCalls, setMaxToolCalls] = useState(6);
  const [timeFilter, setTimeFilter] = useState<number | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [hint, setHint] = useState<string | null>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const blocked = blockedSubmitMessage({
      value: question,
      emptyMessage: EMPTY_QUESTION_MESSAGE,
      busy: isLoading || isPlanning,
      hydrated,
    });
    if (blocked) {
      setHint(blocked);
      return;
    }
    setHint(null);
    onResearch({
      question: question.trim(),
      maxSteps,
      maxToolCalls,
      timeFilter,
    });
  };

  const handlePlanClick = () => {
    const blocked = blockedSubmitMessage({
      value: question,
      emptyMessage: EMPTY_PLAN_QUESTION_MESSAGE,
      busy: isLoading || isPlanning,
      hydrated,
    });
    if (blocked) {
      setHint(blocked);
      return;
    }
    setHint(null);
    onPreviewPlan?.(question.trim(), timeFilter);
  };

  const applyTemplate = (item: (typeof EXAMPLE_QUERIES)[0]) => {
    setQuestion(item.query);
    setTimeFilter(item.year);
  };

  return (
    <div className="w-full bg-slate-900/90 border border-slate-800 rounded-2xl p-5 shadow-2xl backdrop-blur-xl">
      <form onSubmit={handleSubmit} data-search-ready={hydrated ? "true" : "false"} className="space-y-4">
        {/* 输入框主栏 */}
        <div className="relative flex items-center">
          <div className="absolute left-4 text-indigo-400">
            <Compass className="w-5 h-5 animate-pulse" />
          </div>
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="输入复杂历史考据课题（如：蔡世官在1793年的债务纠纷与东印度公司的谈判过程）..."
            disabled={isLoading || isPlanning || !hydrated}
            className="w-full pl-12 pr-48 py-3.5 bg-slate-950/80 border border-slate-700/80 rounded-xl text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500 transition-all shadow-inner"
          />

          <div className="absolute right-2 flex items-center gap-2">
            <button
              type="button"
              onClick={() => setShowConfig(!showConfig)}
              className={`p-2 rounded-lg text-xs transition-colors border ${
                showConfig
                  ? "bg-indigo-500/20 text-indigo-300 border-indigo-500/40"
                  : "bg-slate-800/80 text-slate-400 border-slate-700/60 hover:text-slate-200"
              }`}
              title="调整研究步骤与工具预算配置"
            >
              <SlidersHorizontal className="w-4 h-4" />
            </button>

            {onPreviewPlan && (
              <button
                type="button"
                onClick={handlePlanClick}
                disabled={isLoading || isPlanning || !hydrated}
                className="px-3 py-2 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700/80 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {isPlanning ? "规划中..." : "任务规划"}
              </button>
            )}

            <button
              type="submit"
              disabled={isLoading || isPlanning || !hydrated}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-xs font-semibold bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 text-white shadow-md shadow-indigo-500/20 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {!hydrated ? (
                <span>准备中…</span>
              ) : isLoading ? (
                <>
                  <Sparkles className="w-3.5 h-3.5 animate-spin" />
                  <span>Agent 研究中...</span>
                </>
              ) : (
                <>
                  <span>开始自主考据</span>
                  <ArrowRight className="w-3.5 h-3.5" />
                </>
              )}
            </button>
          </div>
        </div>
        {hint && (
          <p className="text-xs text-amber-300" data-search-hint="true">
            {hint}
          </p>
        )}

        {/* 高级预算参数配置抽屉 */}
        {showConfig && (
          <div className="p-4 rounded-xl bg-slate-950/60 border border-slate-800/80 grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
            <div>
              <div className="flex justify-between text-slate-300 mb-1">
                <span>最大规划步骤数:</span>
                <span className="font-mono text-indigo-400 font-semibold">{maxSteps} 步</span>
              </div>
              <input
                type="range"
                min="1"
                max="8"
                value={maxSteps}
                onChange={(e) => setMaxSteps(parseInt(e.target.value, 10))}
                className="w-full accent-indigo-500 bg-slate-800 cursor-pointer"
              />
              <span className="text-[10px] text-slate-500">
                Agent 动态拆分的子研究目标数量上限
              </span>
            </div>

            <div>
              <div className="flex justify-between text-slate-300 mb-1">
                <span>工具调用上限 (预算):</span>
                <span className="font-mono text-purple-400 font-semibold">{maxToolCalls} 次</span>
              </div>
              <input
                type="range"
                min="1"
                max="12"
                value={maxToolCalls}
                onChange={(e) => setMaxToolCalls(parseInt(e.target.value, 10))}
                className="w-full accent-purple-500 bg-slate-800 cursor-pointer"
              />
              <span className="text-[10px] text-slate-500">
                严格控制并发与外部考据开销，防止失控循环
              </span>
            </div>

            <div>
              <div className="text-slate-300 mb-1">限定历史年份:</div>
              <div className="flex gap-2">
                {[
                  { label: "全部年份", val: null },
                  { label: "1792 年", val: 1792 },
                  { label: "1793 年", val: 1793 },
                ].map((item) => (
                  <button
                    key={item.label}
                    type="button"
                    onClick={() => setTimeFilter(item.val)}
                    className={`px-2.5 py-1 rounded text-xs transition-colors border ${
                      timeFilter === item.val
                        ? "bg-indigo-500/20 text-indigo-300 border-indigo-500/50 font-medium"
                        : "bg-slate-900 text-slate-400 border-slate-800 hover:text-slate-300"
                    }`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <span className="text-[10px] text-slate-500 mt-1 block">
                将检索范围收敛到特定广州商馆日记卷册
              </span>
            </div>
          </div>
        )}

        {/* 预设典型历史考据范例 */}
        <div className="flex items-center gap-2 flex-wrap pt-1">
          <span className="text-xs text-slate-400 font-medium flex items-center gap-1">
            <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
            <span>研究样例:</span>
          </span>
          {EXAMPLE_QUERIES.map((item, index) => (
            <button
              key={index}
              type="button"
              onClick={() => applyTemplate(item)}
              disabled={isLoading || isPlanning}
              className="text-xs px-2.5 py-1 rounded-full bg-slate-800/60 hover:bg-slate-800 text-slate-300 hover:text-indigo-300 border border-slate-700/50 transition-all"
            >
              {item.title}
            </button>
          ))}
        </div>
      </form>
    </div>
  );
};
