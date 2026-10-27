"use client";

import React from "react";
import { parseAnswerSegments, isCitationInRange, citationLabel } from "@/lib/citations";

interface CitationRichTextProps {
  /** Original answer text, rendered verbatim except for the citation tokens. */
  text: string;
  /** Number of evidence items this answer was generated from. */
  sourceCount: number;
  /** Called only for in-range citation numbers. */
  onCitationActivate?: (index: number) => void;
  /** Applied to the wrapper that holds the text segments. */
  className?: string;
}

/**
 * Renders an answer with its `[证据 #N]` markers as interactive citations.
 *
 * - valid numbers become buttons that navigate to the matching evidence card;
 * - out-of-range numbers stay visible (the reader still sees what the model
 *   wrote) but are **not** clickable and never retargeted at another source;
 * - everything else is plain text, so Chinese text, newlines and Markdown-ish
 *   structure are untouched. Nothing is injected as raw HTML.
 */
export const CitationRichText: React.FC<CitationRichTextProps> = ({
  text,
  sourceCount,
  onCitationActivate,
  className,
}) => {
  const segments = parseAnswerSegments(text ?? "");

  return (
    <span className={className}>
      {segments.map((segment, position) => {
        if (segment.kind === "text") {
          return <React.Fragment key={position}>{segment.text}</React.Fragment>;
        }

        const valid = isCitationInRange(segment.index, sourceCount);
        if (!valid) {
          return (
            <span
              key={position}
              data-citation-index={segment.index}
              data-citation-valid="false"
              title={`该引用编号不存在：本次仅有 ${sourceCount} 条证据`}
              className="inline-flex items-center px-1.5 py-0.5 mx-0.5 rounded text-xs font-semibold bg-rose-500/10 text-rose-300 border border-rose-500/30 align-baseline cursor-not-allowed"
            >
              {segment.text}
            </span>
          );
        }

        return (
          <button
            key={position}
            type="button"
            data-citation-index={segment.index}
            data-citation-valid="true"
            onClick={() => onCitationActivate?.(segment.index)}
            aria-label={`跳转到${citationLabel(segment.index)}的档案证据卡片`}
            className="inline-flex items-center px-1.5 py-0.5 mx-0.5 rounded text-xs font-semibold bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 hover:bg-indigo-500/30 hover:scale-105 focus:outline-none focus-visible:ring-2 focus-visible:ring-indigo-400 transition-all cursor-pointer align-baseline"
          >
            {segment.text}
          </button>
        );
      })}
    </span>
  );
};

export default CitationRichText;
