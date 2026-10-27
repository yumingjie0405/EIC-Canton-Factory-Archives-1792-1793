"use client";

import React from "react";
import { X, FileText, ExternalLink, Quote, Sparkles } from "lucide-react";
import { SNAPersonNeighborEvidence } from "@/types/kg";

interface EvidenceViewerModalProps {
  isOpen: boolean;
  onClose: () => void;
  personName: string;
  neighborName: string;
  relationType: string;
  evidences: SNAPersonNeighborEvidence[];
}

export function EvidenceViewerModal({
  isOpen,
  onClose,
  personName,
  neighborName,
  relationType,
  evidences,
}: EvidenceViewerModalProps) {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4 backdrop-blur-sm animate-in fade-in duration-150">
      <div className="relative flex max-h-[85vh] w-full max-w-2xl flex-col rounded-3xl border border-white/80 bg-white shadow-2xl">
        {/* 头部 */}
        <div className="flex items-center justify-between border-b border-slate-100 p-5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-primary/10 text-primary">
              <FileText className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-900">历史档案证据穿透追溯</h3>
              <p className="text-xs text-slate-500">
                关系考证: <span className="font-medium text-slate-700">{personName}</span> ↔{" "}
                <span className="font-medium text-slate-700">{neighborName}</span> (类型: {relationType})
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-100 hover:text-slate-700"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 证据列表内容 */}
        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {evidences.length === 0 ? (
            <div className="py-8 text-center text-xs text-slate-400">
              该关系尚未绑定文本块详细引用
            </div>
          ) : (
            evidences.map((ev, idx) => (
              <div
                key={ev.id || idx}
                className="flex flex-col gap-2 rounded-2xl border border-slate-100 bg-slate-50/70 p-4 transition-all hover:border-slate-200 hover:bg-slate-50"
              >
                <div className="flex items-center justify-between text-xs">
                  <div className="flex items-center gap-2">
                    <span className="rounded-full bg-slate-200/80 px-2 py-0.5 text-[10px] font-semibold text-slate-700">
                      证据 #{idx + 1}
                    </span>
                    <span className="text-[11px] text-slate-500">
                      来源文本块: <code>Block #{ev.block_id}</code>
                    </span>
                  </div>
                  <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[10px] font-medium text-emerald-700">
                    {ev.evidence_role || "SUPPORTS"}
                  </span>
                </div>

                {/* 引文内容 */}
                {ev.quote_text ? (
                  <div className="flex items-start gap-2 rounded-xl bg-white p-3 text-xs leading-relaxed text-slate-700 border border-slate-200/50 shadow-sm">
                    <Quote className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400" />
                    <p className="italic font-serif">{ev.quote_text}</p>
                  </div>
                ) : (
                  <p className="text-xs text-slate-400 italic">引文文本缺失</p>
                )}

                {/* 联动穿透按钮 */}
                <div className="flex justify-end pt-1">
                  <a
                    href={`/viewer?block_id=${ev.block_id}`}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1.5 rounded-lg border border-primary/20 bg-primary/5 px-2.5 py-1 text-[11px] font-medium text-primary hover:bg-primary/10 transition-colors"
                  >
                    <span>在档案对照阅读器中查看</span>
                    <ExternalLink className="h-3 w-3" />
                  </a>
                </div>
              </div>
            ))
          )}
        </div>

        {/* 底部 */}
        <div className="flex items-center justify-between border-t border-slate-100 bg-slate-50/50 px-5 py-3 rounded-b-3xl text-xs text-slate-500">
          <span>共记录 {evidences.length} 条原文证据</span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-xl bg-slate-900 px-4 py-1.5 text-xs font-medium text-white shadow-sm hover:bg-slate-800"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
