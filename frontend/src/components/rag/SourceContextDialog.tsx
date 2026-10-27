"use client";

import React, { useCallback, useEffect, useState } from "react";
import { AlertCircle, BookOpen, ChevronLeft, ChevronRight, Loader2, Quote, X } from "lucide-react";
import { BlockContextResponse } from "@/types/blockContext";
import { fetchBlockContext } from "@/lib/blocksApi";
import {
  BlockTextSection,
  blockTextSections,
  formatBlockLocation,
  neighbourSummary,
} from "@/lib/blockContext";

interface SourceContextDialogProps {
  isOpen: boolean;
  onClose: () => void;
  documentId: number | null | undefined;
  blockId: number | null | undefined;
  documentName?: string | null;
  /**
   * The excerpt stored on the evidence card. For an opened history record this
   * is the **saved snapshot** and must stay labelled as such; the live archive
   * text below it is read from the database now.
   */
  evidenceQuote?: string | null;
  /** `history` labels the quote as a saved snapshot; `live` as the run's cite. */
  evidenceMode?: "live" | "history";
}

function TextSectionList({ sections }: { sections: BlockTextSection[] }) {
  if (sections.length === 0) {
    return (
      <p className="text-[11px] text-slate-500 italic">
        该区块没有可用的 OCR 转录文本（不补写、不生成）。
      </p>
    );
  }
  return (
    <div className="space-y-2">
      {sections.map((section) => (
        <div key={section.key} className="rounded-lg bg-slate-950/60 border border-slate-800 p-3">
          <p className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">{section.label}</p>
          <p className="text-xs text-slate-200 leading-relaxed whitespace-pre-wrap font-serif select-text">
            {section.text}
          </p>
        </div>
      ))}
    </div>
  );
}

/**
 * Read-only dialog showing the archival block behind one citation.
 *
 * It never renders model output as archival text: the API only returns stored
 * OCR transcriptions, and the evidence quote (possibly a saved snapshot) is
 * shown in its own clearly-labelled block.
 */
export const SourceContextDialog: React.FC<SourceContextDialogProps> = ({
  isOpen,
  onClose,
  documentId,
  blockId,
  documentName,
  evidenceQuote,
  evidenceMode = "live",
}) => {
  const [context, setContext] = useState<BlockContextResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const closeButtonRef = React.useRef<HTMLButtonElement | null>(null);
  const restoreFocusRef = React.useRef<HTMLElement | null>(null);

  const load = useCallback(async () => {
    if (documentId == null || blockId == null) return;
    setIsLoading(true);
    setError(null);
    setContext(null);
    try {
      const data = await fetchBlockContext(blockId, documentId, 1);
      setContext(data);
    } catch (err: any) {
      setError(err?.message || "无法读取档案原文。");
    } finally {
      setIsLoading(false);
    }
  }, [blockId, documentId]);

  useEffect(() => {
    if (isOpen) {
      load();
    } else {
      setContext(null);
      setError(null);
    }
  }, [isOpen, load]);

  useEffect(() => {
    if (!isOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [isOpen, onClose]);

  // Move focus into the dialog when it opens and give it back on close, so
  // Escape and the close button work for keyboard and screen-reader users even
  // when the dialog was opened programmatically.
  useEffect(() => {
    if (!isOpen) return;
    restoreFocusRef.current = document.activeElement as HTMLElement | null;
    closeButtonRef.current?.focus();
    return () => {
      restoreFocusRef.current?.focus?.();
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/70 p-4 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="档案原文上下文"
    >
      <div className="relative flex max-h-[88vh] w-full max-w-3xl flex-col rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl">
        <div className="flex items-start justify-between gap-3 border-b border-slate-800 p-4">
          <div className="flex items-start gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-500/10 text-indigo-300 border border-indigo-500/20">
              <BookOpen className="h-4 w-4" />
            </div>
            <div>
              <h3 className="text-sm font-semibold text-slate-100">档案原文上下文</h3>
              <p className="text-[11px] text-slate-400">
                {context?.document_name || documentName || "档案文献"} · 只读
              </p>
            </div>
          </div>
          <button
            type="button"
            ref={closeButtonRef}
            onClick={onClose}
            aria-label="关闭档案原文上下文"
            className="flex h-8 w-8 items-center justify-center rounded-full text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {/* 证据卡片记录的引文（历史记录中是保存快照） */}
          {evidenceQuote && (
            <div className="rounded-xl border border-slate-700/70 bg-slate-800/40 p-3">
              <p className="text-[10px] uppercase tracking-wide text-slate-400 mb-1">
                {evidenceMode === "history"
                  ? "历史记录中保存的引用快照（保存时写入，未随当前档案变化）"
                  : "本次回答引用的原文片段"}
              </p>
              <div className="flex items-start gap-2">
                <Quote className="mt-0.5 h-3 w-3 shrink-0 text-slate-500" />
                <p className="text-xs text-slate-300 leading-relaxed font-serif italic whitespace-pre-wrap">
                  {evidenceQuote}
                </p>
              </div>
            </div>
          )}

          {isLoading && (
            <div className="flex items-center gap-2 text-xs text-slate-400 py-6">
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
              正在读取档案原文…
            </div>
          )}

          {!isLoading && error && (
            <div className="flex items-start gap-2 rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
              <div className="space-y-1">
                <p>{error}</p>
                <p className="text-rose-200/70">
                  可能原因：该区块已被删除、不属于该文档，或数据库暂时不可用。系统不会用其他文本代替。
                </p>
              </div>
            </div>
          )}

          {!isLoading && !error && context && (
            <>
              <div>
                <p className="text-[11px] text-slate-400 mb-1">
                  {formatBlockLocation(context.block, context.page_number)} · {neighbourSummary(context)}
                </p>
              </div>

              {context.previous.length > 0 && (
                <div className="space-y-2">
                  <p className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-slate-500">
                    <ChevronLeft className="h-3 w-3" /> 同页前文
                  </p>
                  {context.previous.map((block) => (
                    <div key={block.block_id} className="opacity-80">
                      <p className="text-[10px] text-slate-500 mb-1">
                        Block #{block.block_id} · 页内序号 {block.block_index}
                      </p>
                      <TextSectionList sections={blockTextSections(block)} />
                    </div>
                  ))}
                </div>
              )}

              <div className="rounded-xl border border-indigo-500/40 bg-indigo-950/20 p-3 space-y-2">
                <p className="text-[10px] uppercase tracking-wide text-indigo-300">
                  被引用区块（当前档案库中的原文）
                </p>
                <p className="text-[10px] text-slate-500">
                  Block #{context.block.block_id} · 页内序号 {context.block.block_index}
                </p>
                <TextSectionList sections={blockTextSections(context.block)} />
              </div>

              {context.next.length > 0 && (
                <div className="space-y-2">
                  <p className="flex items-center gap-1 text-[10px] uppercase tracking-wide text-slate-500">
                    <ChevronRight className="h-3 w-3" /> 同页后文
                  </p>
                  {context.next.map((block) => (
                    <div key={block.block_id} className="opacity-80">
                      <p className="text-[10px] text-slate-500 mb-1">
                        Block #{block.block_id} · 页内序号 {block.block_index}
                      </p>
                      <TextSectionList sections={blockTextSections(block)} />
                    </div>
                  ))}
                </div>
              )}

              <ul className="text-[10px] text-slate-500 space-y-0.5 border-t border-slate-800 pt-2">
                {context.notes.map((note, index) => (
                  <li key={index}>· {note}</li>
                ))}
              </ul>
            </>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-slate-800 px-4 py-3 text-[11px] text-slate-500">
          <span>
            {evidenceMode === "history"
              ? "历史快照与当前档案原文分别显示，互不覆盖"
              : "原文来自已存档案，未调用模型重新生成"}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs text-slate-200 hover:bg-slate-700"
          >
            关闭
          </button>
        </div>
      </div>
    </div>
  );
};

export default SourceContextDialog;
