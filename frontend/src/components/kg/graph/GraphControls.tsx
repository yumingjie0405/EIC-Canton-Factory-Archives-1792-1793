"use client";

import React from "react";
import { ZoomIn, ZoomOut, RotateCcw, Maximize2, Download } from "lucide-react";

interface GraphControlsProps {
  onZoomIn: () => void;
  onZoomOut: () => void;
  onReset: () => void;
  onFitView?: () => void;
  onExport?: () => void;
}

export function GraphControls({
  onZoomIn,
  onZoomOut,
  onReset,
  onFitView,
  onExport,
}: GraphControlsProps) {
  return (
    <div className="flex items-center gap-1.5 rounded-2xl border border-white/60 bg-white/80 p-1.5 shadow-lg backdrop-blur-xl">
      <button
        type="button"
        onClick={onZoomIn}
        title="放大视图"
        className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-600 transition-all hover:bg-slate-100 hover:text-slate-900 active:scale-95"
      >
        <ZoomIn className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={onZoomOut}
        title="缩小视图"
        className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-600 transition-all hover:bg-slate-100 hover:text-slate-900 active:scale-95"
      >
        <ZoomOut className="h-4 w-4" />
      </button>
      <div className="h-4 w-[1px] bg-slate-200" />
      <button
        type="button"
        onClick={onReset}
        title="重置居中"
        className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-600 transition-all hover:bg-slate-100 hover:text-slate-900 active:scale-95"
      >
        <RotateCcw className="h-4 w-4" />
      </button>
      {onFitView && (
        <button
          type="button"
          onClick={onFitView}
          title="全图自适应"
          className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-600 transition-all hover:bg-slate-100 hover:text-slate-900 active:scale-95"
        >
          <Maximize2 className="h-4 w-4" />
        </button>
      )}
      {onExport && (
        <>
          <div className="h-4 w-[1px] bg-slate-200" />
          <button
            type="button"
            onClick={onExport}
            title="导出当前图谱数据"
            className="flex h-9 w-9 items-center justify-center rounded-xl text-slate-600 transition-all hover:bg-slate-100 hover:text-slate-900 active:scale-95"
          >
            <Download className="h-4 w-4" />
          </button>
        </>
      )}
    </div>
  );
}
