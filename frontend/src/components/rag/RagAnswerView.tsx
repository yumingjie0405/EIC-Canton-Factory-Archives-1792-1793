"use client";

import React from "react";
import { CheckCircle2, AlertTriangle, Clock, Layers, Sparkles, BookOpen } from "lucide-react";
import { RagQueryResponse } from "@/types/rag";
import { GenerationStatusBanner } from "@/components/ui/generation-status";
import { CitationRichText } from "@/components/rag/CitationRichText";

interface RagAnswerViewProps {
  response: RagQueryResponse;
  /** Called with the 1-based citation number of a valid `[证据 #N]` marker. */
  onCitationActivate?: (index: number) => void;
}

export const RagAnswerView: React.FC<RagAnswerViewProps> = ({ response, onCitationActivate }) => {
  const { answer, evidence_sufficient, intent, execution_time_ms, sources, entities, generation } = response;

  return (
    <div className="w-full bg-slate-900/90 border border-slate-800 rounded-2xl p-6 shadow-xl relative overflow-hidden">
      {/* 顶部状态与意图指示栏 */}
      <div className="flex flex-wrap items-center justify-between gap-3 pb-4 mb-4 border-b border-slate-800/80">
        <div className="flex items-center gap-2">
          {evidence_sufficient ? (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-xs font-medium">
              <CheckCircle2 className="w-3.5 h-3.5" />
              <span>档案证据确凿</span>
            </div>
          ) : (
            <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 text-xs font-medium">
              <AlertTriangle className="w-3.5 h-3.5" />
              <span>证据不足·主动拒答</span>
            </div>
          )}

          <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-slate-800 text-slate-300 border border-slate-700 text-xs">
            <Layers className="w-3.5 h-3.5 text-indigo-400" />
            <span>
              意图:{" "}
              {intent.query_type === "factual"
                ? "事实考据"
                : intent.query_type === "relational"
                ? "社会网络关系"
                : "历史深度分析"}
            </span>
          </div>

          {intent.time_filter && (
            <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700/60 text-xs">
              年份: {intent.time_filter}
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5 text-xs text-slate-400">
          <Clock className="w-3.5 h-3.5" />
          <span>耗时: {execution_time_ms} ms</span>
        </div>
      </div>

      {/* 生成来源与引用校验状态（模型不可用时必须显式可见） */}
      <GenerationStatusBanner generation={generation} className="pb-4 mb-1" />

      {/* AI 回答文本主体 */}
      <div className="prose prose-invert max-w-none">
        <div className="flex items-start gap-3">
          <div className="p-2 rounded-xl bg-gradient-to-br from-indigo-500/20 to-purple-500/20 border border-indigo-500/30 text-indigo-400 mt-1 shrink-0">
            <Sparkles className="w-5 h-5" />
          </div>
          <div className="flex-1 text-slate-200 text-sm leading-relaxed whitespace-pre-wrap font-sans">
            <CitationRichText
              text={answer}
              sourceCount={sources.length}
              onCitationActivate={onCitationActivate}
            />
          </div>
        </div>
      </div>

      {/* 底部引用元数据统计 */}
      <div className="mt-6 pt-4 border-t border-slate-800/60 flex flex-wrap items-center justify-between gap-4 text-xs text-slate-400">
        <div className="flex items-center gap-4">
          <div className="flex items-center gap-1.5">
            <BookOpen className="w-3.5 h-3.5 text-slate-500" />
            <span>引用档案文献: <strong className="text-slate-200">{sources.length}</strong> 处</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Layers className="w-3.5 h-3.5 text-slate-500" />
            <span>涉及图谱实体: <strong className="text-slate-200">{entities.length}</strong> 位</span>
          </div>
        </div>

        <div className="text-[11px] text-slate-500 italic">
          * 遵循历史学严格考证原则：无档案支持结论一律拒答，杜绝大模型学术幻觉
        </div>
      </div>
    </div>
  );
};
