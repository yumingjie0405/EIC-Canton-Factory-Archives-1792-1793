"use client";

import React from "react";
import { GenerationStatusBanner } from "@/components/ui/generation-status";
import { CitationRichText } from "@/components/rag/CitationRichText";
import { SourceCardList } from "@/components/rag/SourceCardList";
import { useCitationNavigation } from "@/lib/useCitationNavigation";
import { ResearchRecordDetail } from "@/types/research";

interface ResearchRecordViewProps {
  /** A saved record; its evidence snapshot is rendered as stored. */
  detail: ResearchRecordDetail;
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

/**
 * One opened history record: stored answer + stored evidence snapshot.
 *
 * The citation scope is derived from the record id, so two opened records (or a
 * record plus the live RAG/Agent result) keep independent anchors. Nothing here
 * re-runs retrieval or the model — the backend returns the saved snapshot and
 * this component only renders it.
 */
export const ResearchRecordView: React.FC<ResearchRecordViewProps> = ({ detail }) => {
  const scope = `history-${detail.id}`;
  const { activeIndex, activate } = useCitationNavigation(scope, detail.sources.length);

  return (
    <div className="space-y-3" data-history-record={detail.id}>
      <div className="rounded-xl bg-slate-950/60 border border-slate-800 p-3">
        <p className="text-[11px] text-slate-500 mb-1">
          保存于 {formatTime(detail.created_at)} 的历史回答（未重新检索、未重新调用模型）
        </p>
        <div className="text-xs text-slate-200 leading-relaxed whitespace-pre-wrap">
          {detail.answer ? (
            <CitationRichText
              text={detail.answer}
              sourceCount={detail.sources.length}
              onCitationActivate={activate}
            />
          ) : (
            "（该记录没有回答正文）"
          )}
        </div>
      </div>

      <GenerationStatusBanner generation={detail.generation} />

      {detail.sources.length > 0 && (
        <SourceCardList
          sources={detail.sources}
          scope={scope}
          highlightedIndex={activeIndex}
          evidenceMode="history"
        />
      )}

      {detail.warnings.length > 0 && (
        <ul className="text-[11px] text-amber-300/80 space-y-0.5">
          {detail.warnings.map((warning, index) => (
            <li key={index}>· {warning}</li>
          ))}
        </ul>
      )}
    </div>
  );
};

export default ResearchRecordView;
