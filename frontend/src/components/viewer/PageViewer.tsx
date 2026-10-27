"use client";

import { useRef, useEffect, useState } from "react";
import { Block, Page } from "@/types";
import { BboxOverlay } from "./BboxOverlay";
import { pageApi } from "@/lib/api";
import { Loader2, ScanText, ImageIcon } from "lucide-react";

interface PageViewerProps {
  page: Page;
  imageUrlOverride?: string;
  blocks: Block[];
  selectedBlockId: number | null;
  targetBlockId?: number | null;
  onBlockSelect: (block: Block) => void;
}

interface RenderedImageRect {
  width: number;
  height: number;
  left: number;
  top: number;
}

export function PageViewer({
  page,
  imageUrlOverride,
  blocks,
  selectedBlockId,
  targetBlockId,
  onBlockSelect,
}: PageViewerProps) {
  const stageRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const [renderedImageRect, setRenderedImageRect] = useState<RenderedImageRect>(
    {
      width: 0,
      height: 0,
      left: 0,
      top: 0,
    },
  );
  const [imageLoaded, setImageLoaded] = useState(false);

  useEffect(() => {
    setImageLoaded(false);
    setRenderedImageRect({ width: 0, height: 0, left: 0, top: 0 });
  }, [page.id, imageUrlOverride]);

  useEffect(() => {
    if (!imageLoaded) return;

    const updateRenderedImageRect = () => {
      if (!frameRef.current || !imageRef.current) return;

      const frameBounds = frameRef.current.getBoundingClientRect();
      const imageBounds = imageRef.current.getBoundingClientRect();
      setRenderedImageRect({
        width: imageBounds.width,
        height: imageBounds.height,
        left: imageBounds.left - frameBounds.left,
        top: imageBounds.top - frameBounds.top,
      });
    };

    updateRenderedImageRect();
    const resizeObserver = new ResizeObserver(updateRenderedImageRect);
    if (frameRef.current) {
      resizeObserver.observe(frameRef.current);
    }
    if (imageRef.current) {
      resizeObserver.observe(imageRef.current);
    }
    window.addEventListener("resize", updateRenderedImageRect);

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener("resize", updateRenderedImageRect);
    };
  }, [imageLoaded, page.id]);

  const imageUrl = imageUrlOverride || pageApi.getImageUrl(page.id);
  // Trust the API's page.width/height — they now match the stored high-res image
  // and the bbox coordinate canvas. Using naturalWidth would break alignment if
  // the browser scales the image differently.
  const imageWidth = page.width || 800;
  const imageHeight = page.height || 1000;

  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-white/80 shadow-none">
      <div className="flex flex-col gap-3 border-b border-slate-200/80 px-5 py-4 md:flex-row md:items-center md:justify-between">
        <div>
          <p className="text-sm font-medium text-slate-900">
            第 {page.page_number} 页
          </p>
          <p className="text-xs text-slate-500">
            原始尺寸 {imageWidth} × {imageHeight} · {blocks.length} 个识别区块
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
          <div className="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1">
            <ScanText className="h-3.5 w-3.5" />
            OCR Overlay
          </div>
          <div className="inline-flex items-center gap-2 rounded-full bg-primary/10 px-3 py-1 text-primary">
            <ImageIcon className="h-3.5 w-3.5" />
            点击高亮框可联动右侧文本
          </div>
        </div>
      </div>

      <div
        ref={stageRef}
        className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain bg-muted/40 p-3"
      >
        {!imageLoaded && (
          <div className="absolute inset-0 flex items-center justify-center">
            <Loader2 className="h-8 w-8 animate-spin text-primary" />
          </div>
        )}

        <div
          ref={frameRef}
          className="relative flex w-full items-start justify-center rounded border border-border bg-white"
        >
          <img
            key={page.id}
            ref={imageRef}
            src={imageUrl}
            alt={`Page ${page.page_number}`}
            className="h-auto w-full object-contain"
            onLoad={() => {
              setImageLoaded(true);
            }}
            style={{ display: imageLoaded ? "block" : "none" }}
          />

          {imageLoaded && renderedImageRect.width > 0 && (
            <BboxOverlay
              blocks={blocks}
              selectedBlockId={selectedBlockId}
              targetBlockId={targetBlockId}
              onBlockClick={onBlockSelect}
              imageWidth={imageWidth}
              imageHeight={imageHeight}
              containerWidth={renderedImageRect.width}
              containerHeight={renderedImageRect.height}
              offsetX={renderedImageRect.left}
              offsetY={renderedImageRect.top}
            />
          )}
        </div>
      </div>
    </div>
  );
}
