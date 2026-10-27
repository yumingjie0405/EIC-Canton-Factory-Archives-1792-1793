"use client";

import React, { useState } from "react";
import {
  FileText,
  Network,
  BookMarked,
  Lightbulb,
  HelpCircle,
  Sparkles,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { AnswerCategorization } from "@/types/agent";
import { CitationRichText } from "@/components/rag/CitationRichText";

interface AgentCategorizedAnswerViewProps {
  finalAnswer: string;
  categorization: AnswerCategorization;
  /** Number of evidence items the run returned; used to validate citation numbers. */
  sourceCount: number;
  /** Called with the 1-based citation number of a valid `[证据 #N]` marker. */
  onCitationActivate?: (index: number) => void;
}

export const AgentCategorizedAnswerView: React.FC<AgentCategorizedAnswerViewProps> = ({
  finalAnswer,
  categorization,
  sourceCount,
  onCitationActivate,
}) => {
  const [showRawAnswer, setShowRawAnswer] = useState(false);

  const {
    archival_facts = [],
    kg_relationships = [],
    external_scholarship = [],
    inferences_and_synthesis = [],
    unresolved_aspects = [],
  } = categorization || {};

  // 回答正文与四层分类条目共用同一套引用解析/导航逻辑（与 RAG、历史详情一致）
  const renderFormattedText = (text: string) => (
    <CitationRichText
      text={text}
      sourceCount={sourceCount}
      onCitationActivate={onCitationActivate}
    />
  );

  return (
    <div className="w-full bg-slate-900/90 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">
      {/* 标题栏 */}
      <div className="flex items-center justify-between pb-3 border-b border-slate-800/80">
        <div className="flex items-center gap-2">
          <div className="p-2 rounded-xl bg-gradient-to-br from-indigo-500/20 to-purple-500/20 border border-indigo-500/30 text-indigo-400">
            <Sparkles className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-base font-semibold text-slate-100">
              四层学术考据结论 (防幻觉分层综述)
            </h2>
            <p className="text-xs text-slate-400">
              严格区隔一手档案文本、图谱结构事实、外部权威考据与历史推断
            </p>
          </div>
        </div>

        <button
          type="button"
          onClick={() => setShowRawAnswer(!showRawAnswer)}
          className="flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300 transition-colors px-2 py-1 rounded-lg bg-indigo-500/10 border border-indigo-500/20"
        >
          <span>{showRawAnswer ? "收起全文答复" : "查看完整文本报告"}</span>
          {showRawAnswer ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        </button>
      </div>

      {/* 完整文本答复展示 (可折叠) */}
      {showRawAnswer && (
        <div className="p-4 rounded-xl bg-slate-950/70 border border-slate-800 text-xs text-slate-300 leading-relaxed font-sans whitespace-pre-wrap select-text">
          {renderFormattedText(finalAnswer)}
        </div>
      )}

      {/* 四层分层结论网格 */}
      <div className="space-y-4">
        {/* 1. 档案直接记载 */}
        <div className="p-4 rounded-xl bg-indigo-950/30 border border-indigo-500/30 space-y-2">
          <div className="flex items-center gap-2 text-xs font-semibold text-indigo-300">
            <FileText className="w-4 h-4 text-indigo-400" />
            <span>【第一层：档案直接记载】(一手文献与逐字文本引注)</span>
            <span className="text-[11px] font-normal text-indigo-400/80 font-mono">
              ({archival_facts.length} 项)
            </span>
          </div>
          {archival_facts.length > 0 ? (
            <ul className="space-y-2 text-xs text-slate-200">
              {archival_facts.map((fact, i) => (
                <li key={i} className="flex items-start gap-2 leading-relaxed">
                  <span className="text-indigo-400 mt-0.5">•</span>
                  <div className="flex-1">{renderFormattedText(fact)}</div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-slate-400 italic">
              在当前档案卷册中暂未检索到直接对应记录。
            </p>
          )}
        </div>

        {/* 2. 图谱关系事实 */}
        <div className="p-4 rounded-xl bg-purple-950/30 border border-purple-500/30 space-y-2">
          <div className="flex items-center gap-2 text-xs font-semibold text-purple-300">
            <Network className="w-4 h-4 text-purple-400" />
            <span>【第二层：知识图谱结构事实】(已核验行商·船长·大班网络)</span>
            <span className="text-[11px] font-normal text-purple-400/80 font-mono">
              ({kg_relationships.length} 项)
            </span>
          </div>
          {kg_relationships.length > 0 ? (
            <ul className="space-y-2 text-xs text-slate-200">
              {kg_relationships.map((rel, i) => (
                <li key={i} className="flex items-start gap-2 leading-relaxed">
                  <span className="text-purple-400 mt-0.5">•</span>
                  <div className="flex-1">{renderFormattedText(rel)}</div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-slate-400 italic">
              知识图谱未收录该维度对应实体关联。
            </p>
          )}
        </div>

        {/* 3. 外部学术考据 */}
        <div className="p-4 rounded-xl bg-emerald-950/30 border border-emerald-500/30 space-y-2">
          <div className="flex items-center gap-2 text-xs font-semibold text-emerald-300">
            <BookMarked className="w-4 h-4 text-emerald-400" />
            <span>【第三层：外部权威考据】(梁嘉彬《广东十三行考》等权威史志)</span>
            <span className="text-[11px] font-normal text-emerald-400/80 font-mono">
              ({external_scholarship.length} 项)
            </span>
          </div>
          {external_scholarship.length > 0 ? (
            <ul className="space-y-2 text-xs text-slate-200">
              {external_scholarship.map((schol, i) => (
                <li key={i} className="flex items-start gap-2 leading-relaxed">
                  <span className="text-emerald-400 mt-0.5">•</span>
                  <div className="flex-1">{renderFormattedText(schol)}</div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-slate-400 italic">
              暂未关联到外部标准权威书目条目。
            </p>
          )}
        </div>

        {/* 4. 历史分析与综合推断 */}
        <div className="p-4 rounded-xl bg-amber-950/20 border border-amber-500/30 space-y-2">
          <div className="flex items-center gap-2 text-xs font-semibold text-amber-300">
            <Lightbulb className="w-4 h-4 text-amber-400" />
            <span>【第四层：历史分析与综合推断】(逻辑归纳与背景阐释，非一手实录)</span>
            <span className="text-[11px] font-normal text-amber-400/80 font-mono">
              ({inferences_and_synthesis.length} 项)
            </span>
          </div>
          {inferences_and_synthesis.length > 0 ? (
            <ul className="space-y-2 text-xs text-slate-200">
              {inferences_and_synthesis.map((inf, i) => (
                <li key={i} className="flex items-start gap-2 leading-relaxed">
                  <span className="text-amber-400 mt-0.5">•</span>
                  <div className="flex-1">{renderFormattedText(inf)}</div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-slate-400 italic">
              本查询结论完全由第一手文献直接证实，未作延伸推论。
            </p>
          )}
        </div>

        {/* 5. 档案未见记录与存疑说明 (如果存在) */}
        {unresolved_aspects && unresolved_aspects.length > 0 && (
          <div className="p-4 rounded-xl bg-rose-950/20 border border-rose-500/30 space-y-2">
            <div className="flex items-center gap-2 text-xs font-semibold text-rose-300">
              <HelpCircle className="w-4 h-4 text-rose-400" />
              <span>【档案未见记录与存疑说明】(历史盲区·审慎拒答依据)</span>
            </div>
            <ul className="space-y-2 text-xs text-rose-200/90">
              {unresolved_aspects.map((item, i) => (
                <li key={i} className="flex items-start gap-2 leading-relaxed">
                  <span className="text-rose-400 mt-0.5">•</span>
                  <div className="flex-1">{renderFormattedText(item)}</div>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
};
