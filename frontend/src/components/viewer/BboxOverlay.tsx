"use client";

import { Block } from "@/types";
import { cn } from "@/lib/utils";

interface BboxOverlayProps {
  blocks: Block[];
  selectedBlockId: number | null;
  targetBlockId?: number | null;
  onBlockClick: (block: Block) => void;
  imageWidth: number;
  imageHeight: number;
  containerWidth: number;
  containerHeight: number;
  offsetX: number;
  offsetY: number;
}

export function BboxOverlay({
  blocks,
  selectedBlockId,
  targetBlockId,
  onBlockClick,
  imageWidth,
  imageHeight,
  containerWidth,
  containerHeight,
  offsetX,
  offsetY,
}: BboxOverlayProps) {
  const safeImageWidth = imageWidth > 0 ? imageWidth : 1;
  const safeImageHeight = imageHeight > 0 ? imageHeight : 1;
  const scaleX = containerWidth / safeImageWidth;
  const scaleY = containerHeight / safeImageHeight;

  const getBlockStyle = (block: Block) => {
    const [x1 = 0, y1 = 0, x2 = 0, y2 = 0] = block.bbox || [];
    const left = offsetX + x1 * scaleX;
    const top = offsetY + y1 * scaleY;
    const width = Math.max((x2 - x1) * scaleX, 2);
    const height = Math.max((y2 - y1) * scaleY, 2);

    return {
      left: `${left}px`,
      top: `${top}px`,
      width: `${width}px`,
      height: `${height}px`,
    };
  };

  const getLabelColor = (label: string | undefined) => {
    const colors: Record<string, string> = {
      text: "border-sky-500/80 bg-sky-500/10",
      title: "border-violet-500/80 bg-violet-500/10",
      figure: "border-emerald-500/80 bg-emerald-500/10",
      table: "border-amber-500/80 bg-amber-500/10",
      list: "border-cyan-500/80 bg-cyan-500/10",
      header: "border-pink-500/80 bg-pink-500/10",
      footer: "border-slate-500/80 bg-slate-500/10",
      markdown_fallback: "border-primary/70 bg-primary/10",
    };
    return colors[label || "text"] || colors.text;
  };

  return (
    <div className="pointer-events-none absolute inset-0">
      {blocks.map((block) => (
        <button
          key={block.id}
          type="button"
          className={cn(
            "group absolute overflow-visible rounded-md border-2 pointer-events-auto transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/60 focus-visible:ring-offset-2",
            getLabelColor(block.block_label),
            block.id === targetBlockId
              ? "border-indigo-600 bg-indigo-500/25 ring-4 ring-indigo-500/40 shadow-[0_0_15px_rgba(79,70,229,0.5)] z-20"
              : selectedBlockId === block.id
              ? "border-primary bg-primary/15 shadow-[0_0_0_4px_rgba(99,102,241,0.18)] z-10"
              : "hover:border-primary/80 hover:bg-primary/10"
          )}
          style={getBlockStyle(block)}
          onClick={() => onBlockClick(block)}
          title={`${block.block_label || "text"} - Block ${block.block_index + 1}`}
        >
          <span
            className={cn(
              "absolute -top-6 left-0 rounded-full px-2 py-0.5 text-[11px] font-medium shadow-lg transition-all",
              block.id === targetBlockId
                ? "bg-indigo-600 text-white font-semibold ring-2 ring-white/80"
                : "bg-slate-950 text-white"
            )}
          >
            {block.id === targetBlockId ? `★ 目标切片 #${block.id}` : `#${block.block_index + 1}`}
          </span>
          <span className="absolute inset-0 rounded-[inherit] bg-white/10 opacity-0 transition-opacity duration-200 group-hover:opacity-100 group-focus-visible:opacity-100" />
        </button>
      ))}
    </div>
  );
}
