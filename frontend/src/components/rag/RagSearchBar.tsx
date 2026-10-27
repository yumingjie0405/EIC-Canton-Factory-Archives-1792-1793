"use client";

import React, { useState } from "react";
import { Search, Sparkles, SlidersHorizontal, HelpCircle } from "lucide-react";
import { useHydrated } from "@/lib/useHydrated";
import { EMPTY_QUESTION_MESSAGE, blockedSubmitMessage } from "@/lib/formReadiness";

interface RagSearchBarProps {
  onSearch: (question: string, includeGraph: boolean, timeFilter?: number | null) => void;
  isLoading: boolean;
}

const PRESET_QUESTIONS = [
  {
    title: "事实查询",
    query: "浩官是谁？在广州十三行中承担什么角色？",
  },
  {
    title: "关系查询",
    query: "Henry Browne 与 Howqua 之间有什么往来与商业关系？",
  },
  {
    title: "深度分析",
    query: "1792年东印度公司与行商之间的毛织品贸易是如何开展的？",
  },
  {
    title: "防幻觉拒答测试",
    query: "拿破仑在广州十三行商馆进行了什么军事演习？",
  },
];

export const RagSearchBar: React.FC<RagSearchBarProps> = ({ onSearch, isLoading }) => {
  const hydrated = useHydrated();
  const [question, setQuestion] = useState("");
  const [includeGraph, setIncludeGraph] = useState(true);
  const [timeFilter, setTimeFilter] = useState<string>("all");
  const [showFilters, setShowFilters] = useState(false);
  const [hint, setHint] = useState<string | null>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const blocked = blockedSubmitMessage({
      value: question,
      emptyMessage: EMPTY_QUESTION_MESSAGE,
      busy: isLoading,
      hydrated,
    });
    if (blocked) {
      setHint(blocked);
      return;
    }
    setHint(null);
    const year = timeFilter === "all" ? null : parseInt(timeFilter, 10);
    onSearch(question.trim(), includeGraph, year);
  };

  const handleSelectPreset = (q: string) => {
    setQuestion(q);
    const year = timeFilter === "all" ? null : parseInt(timeFilter, 10);
    onSearch(q, includeGraph, year);
  };

  return (
    <div className="w-full bg-slate-900/80 backdrop-blur-md border border-slate-800 rounded-2xl p-6 shadow-xl">
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-2">
          <div className="p-2 rounded-lg bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">
            <Sparkles className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-lg font-semibold text-slate-100">历史档案 Graph RAG 智能问答</h2>
            <p className="text-xs text-slate-400">结合 1792-1793 英国东印度公司商馆档案、知识图谱与外部考证规范</p>
          </div>
        </div>

        <button
          type="button"
          onClick={() => setShowFilters(!showFilters)}
          className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition-colors ${
            showFilters
              ? "bg-indigo-600/20 text-indigo-300 border-indigo-500/40"
              : "bg-slate-800 text-slate-300 border-slate-700 hover:bg-slate-750"
          }`}
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          <span>检索控制</span>
        </button>
      </div>

      <form onSubmit={handleSubmit} data-search-ready={hydrated ? "true" : "false"} className="relative">
        <div className="relative flex items-center">
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="输入关于东印度公司商馆、行商人物或历史事件的问题..."
            className="w-full pl-12 pr-28 py-3.5 bg-slate-950/70 border border-slate-700/80 rounded-xl text-slate-100 placeholder-slate-500 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500/50 focus:border-indigo-500 transition-all shadow-inner"
            disabled={isLoading || !hydrated}
          />
          <Search className="w-5 h-5 text-slate-400 absolute left-4 pointer-events-none" />

          <button
            type="submit"
            disabled={isLoading || !hydrated}
            className="absolute right-2 px-5 py-2 bg-gradient-to-r from-indigo-600 to-indigo-700 hover:from-indigo-500 hover:to-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed text-white text-xs font-medium rounded-lg shadow-md transition-all flex items-center gap-2"
          >
            {!hydrated ? (
              <span>准备中…</span>
            ) : isLoading ? (
              <>
                <div className="w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                <span>检索中...</span>
              </>
            ) : (
              <span>智能检索</span>
            )}
          </button>
        </div>
        {hint && (
          <p className="mt-2 text-xs text-amber-300" data-search-hint="true">
            {hint}
          </p>
        )}

        {/* 检索高级过滤配置 */}
        {showFilters && (
          <div className="mt-3 p-3.5 bg-slate-950/40 border border-slate-800/80 rounded-xl flex flex-wrap items-center gap-6 text-xs text-slate-300 animate-fadeIn">
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={includeGraph}
                onChange={(e) => setIncludeGraph(e.target.checked)}
                className="rounded border-slate-700 bg-slate-900 text-indigo-600 focus:ring-indigo-500/30"
              />
              <span>融合结构化知识图谱 (SNA与社会关系)</span>
            </label>

            <div className="flex items-center gap-2">
              <span className="text-slate-400">档案年份约束:</span>
              <select
                value={timeFilter}
                onChange={(e) => setTimeFilter(e.target.value)}
                className="bg-slate-900 border border-slate-700 rounded-md px-2 py-1 text-slate-200 text-xs focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                <option value="all">全部年份 (1792-1793)</option>
                <option value="1792">1792 年 (乾隆五十七年)</option>
                <option value="1793">1793 年 (马戛尔尼使团抵粤年)</option>
              </select>
            </div>
          </div>
        )}
      </form>

      {/* 预设历史研讨问题胶囊 */}
      <div className="mt-4 pt-3 border-t border-slate-800/60">
        <div className="flex items-center gap-1.5 text-xs text-slate-400 mb-2">
          <HelpCircle className="w-3.5 h-3.5 text-slate-500" />
          <span>推荐历史问题探索:</span>
        </div>
        <div className="flex flex-wrap gap-2">
          {PRESET_QUESTIONS.map((item, idx) => (
            <button
              key={idx}
              type="button"
              onClick={() => handleSelectPreset(item.query)}
              disabled={isLoading}
              className="text-left px-3 py-1.5 rounded-lg bg-slate-800/60 hover:bg-slate-800 border border-slate-700/60 hover:border-slate-600 text-slate-300 text-xs transition-all hover:text-indigo-300 flex items-center gap-1.5 group"
            >
              <span className="px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400 text-[10px] border border-indigo-500/20 font-mono">
                {item.title}
              </span>
              <span className="group-hover:translate-x-0.5 transition-transform">{item.query}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
};
