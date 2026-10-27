"use client";

import React, { useState } from "react";
import Link from "next/link";
import { BookOpen, FileText, ExternalLink, Bookmark, CheckCheck } from "lucide-react";
import { RagSourceEvidence } from "@/types/rag";
import { citationLabel, evidenceAnchorId, normalizeScope } from "@/lib/citations";
import { checkEvidenceLocation } from "@/lib/blockContext";
import { SourceContextDialog } from "@/components/rag/SourceContextDialog";

interface SourceCardListProps {
  sources: RagSourceEvidence[];
  /**
   * Namespaces the DOM anchors of these cards. Every research result (current
   * RAG answer, current Agent run, one saved history record) uses its own scope
   * so that several results on the same page never share an anchor id.
   */
  scope: string;
  /** 1-based citation number to highlight, or null. */
  highlightedIndex?: number | null;
  /**
   * `history` marks the card excerpts as saved snapshots, so the context dialog
   * can label them apart from the live archive text it reads.
   */
  evidenceMode?: "live" | "history";
}

export const SourceCardList: React.FC<SourceCardListProps> = ({
  sources,
  scope,
  highlightedIndex = null,
  evidenceMode = "live",
}) => {
  const safeScope = normalizeScope(scope);
  const [contextSource, setContextSource] = useState<{
    blockId: number;
    documentId: number;
    documentName: string;
    quote: string;
  } | null>(null);
  const [contextNotice, setContextNotice] = useState<string | null>(null);

  const openContext = (source: RagSourceEvidence) => {
    const check = checkEvidenceLocation(source);
    if (!check.ok) {
      setContextNotice(check.reason || "无法定位该证据的档案原文。");
      return;
    }
    setContextNotice(null);
    setContextSource({
      blockId: Number(source.block_id),
      documentId: Number(source.document_id),
      documentName: source.document_name,
      quote: source.quote_text,
    });
  };

  if (!sources || sources.length === 0) {
    return (
      <div className="bg-slate-900/60 border border-slate-800 rounded-2xl p-6 text-center text-slate-400 text-sm">
        <Bookmark className="w-8 h-8 text-slate-600 mx-auto mb-2" />
        <p>当前查询未检索到有效档案文本来源。</p>
      </div>
    );
  }

  return (
    <div className="space-y-3" data-evidence-list={safeScope}>
      {/* Screen-reader announcement when the highlighted citation changes. */}
      <p className="sr-only" aria-live="polite">
        {highlightedIndex ? `已定位到${citationLabel(highlightedIndex)}` : ""}
      </p>

      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
          <FileText className="w-4 h-4 text-indigo-400" />
          <span>档案文献引用证据链 (四级真实穿透溯源)</span>
        </h3>
        <span className="text-xs text-slate-500 font-mono">共 {sources.length} 条引证</span>
      </div>

      <div className="space-y-3">
        {sources.map((src, idx) => {
          // Position is the citation number: the backend numbers `证据 #N` by
          // array position, never by the stored `source_id` string.
          const citationIndex = idx + 1;
          const isHighlighted = highlightedIndex === citationIndex;
          const storedId = src.source_id;
          const showStoredId = Boolean(storedId) && storedId !== citationLabel(citationIndex);

          return (
            <div
              key={citationIndex}
              id={evidenceAnchorId(safeScope, citationIndex)}
              data-evidence-scope={safeScope}
              data-citation-index={citationIndex}
              data-highlighted={isHighlighted ? "true" : "false"}
              tabIndex={-1}
              aria-current={isHighlighted ? "true" : undefined}
              aria-label={`${citationLabel(citationIndex)}，来源文献 ${src.document_name}`}
              className={`p-4 rounded-xl border transition-all duration-300 relative outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 ${
                isHighlighted
                  ? "bg-indigo-950/40 border-indigo-500/80 ring-2 ring-indigo-500/30 shadow-lg shadow-indigo-900/20"
                  : "bg-slate-900/70 border-slate-800/80 hover:border-slate-700 hover:bg-slate-900"
              }`}
            >
              {/* 卡片头部：引证编号与文档信息 */}
              <div className="flex items-center justify-between gap-2 mb-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-300 border border-indigo-500/30 text-xs font-mono font-medium">
                    {citationLabel(citationIndex)}
                  </span>

                  {showStoredId && (
                    <span
                      className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700 text-[11px] font-mono"
                      title="检索器记录的原始来源标识（与引用编号不同）"
                    >
                      {storedId}
                    </span>
                  )}

                  <span className="text-xs font-medium text-slate-200 truncate max-w-xs" title={src.document_name}>
                    {src.document_name}
                  </span>

                  {src.temporal_coverage && (
                    <span className="px-1.5 py-0.5 rounded bg-amber-950/60 text-amber-300 border border-amber-800/60 text-[11px] font-sans">
                      {src.temporal_coverage}
                    </span>
                  )}

                  {typeof src.page_number === "number" ? (
                    <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700 text-[11px]">
                      第 {src.page_number} 页
                    </span>
                  ) : (
                    <span className="px-1.5 py-0.5 rounded bg-slate-800/60 text-slate-500 border border-slate-700/60 text-[11px]">
                      页码未记录
                    </span>
                  )}

                  {typeof src.block_id === "number" && src.block_id > 0 && (
                    <span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700 text-[11px] font-mono">
                      Block #{src.block_id}
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-[11px] text-emerald-400 font-mono bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20">
                    相关度: {(src.relevance_score * 100).toFixed(1)}%
                  </span>

                  <button
                    type="button"
                    onClick={() => openContext(src)}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded-md bg-slate-800 text-slate-300 hover:bg-indigo-500/20 hover:text-indigo-200 border border-slate-700 text-[11px] transition-colors"
                    title="查看该引用在档案中的原文及同页相邻区块"
                  >
                    <BookOpen className="w-3 h-3" />
                    <span>查看档案上下文</span>
                  </button>

                  {typeof src.block_id === "number" && src.block_id > 0 && (
                    <Link
                      href={
                        src.document_id
                          ? `/viewer/${src.document_id}?block_id=${src.block_id}${src.page_number ? `&page=${src.page_number}` : ""}`
                          : `/viewer?block_id=${src.block_id}`
                      }
                      target="_blank"
                      className="p-1 rounded-md text-slate-400 hover:text-indigo-300 hover:bg-slate-800 transition-colors"
                      title="在档案文献阅览器中定位原始页面与标注框"
                    >
                      <ExternalLink className="w-3.5 h-3.5" />
                    </Link>
                  )}
                </div>
              </div>

              {/* 原文引语内容 */}
              <div className="p-3 rounded-lg bg-slate-950/60 border border-slate-800/80 text-xs text-slate-300 leading-relaxed font-serif italic mb-2 select-text">
                &ldquo;{src.quote_text}&rdquo;
              </div>

              {/* 命中实体或关键词标签 */}
              {src.matched_entities && src.matched_entities.length > 0 && (
                <div className="flex items-center gap-1.5 flex-wrap text-[11px]">
                  <CheckCheck className="w-3 h-3 text-slate-500" />
                  <span className="text-slate-500">匹配要素:</span>
                  {src.matched_entities.map((m, mIdx) => (
                    <span
                      key={mIdx}
                      className="px-1.5 py-0.2 rounded bg-slate-800/80 text-slate-400 border border-slate-700/50"
                    >
                      {m}
                    </span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {contextNotice && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-[11px] text-amber-200">
          {contextNotice}
        </div>
      )}

      <SourceContextDialog
        isOpen={contextSource !== null}
        onClose={() => setContextSource(null)}
        documentId={contextSource?.documentId ?? null}
        blockId={contextSource?.blockId ?? null}
        documentName={contextSource?.documentName ?? null}
        evidenceQuote={contextSource?.quote ?? null}
        evidenceMode={evidenceMode}
      />
    </div>
  );
};
