/**
 * Pure helpers for the archival block context dialog.
 *
 * No React, no DOM: these decide *what can be shown* before any network call,
 * so "cannot locate this block" never turns into a fabricated context view.
 */
import type { BlockContextBlock, BlockContextResponse } from "../types/blockContext";

export interface EvidenceLocation {
  document_id?: number | null;
  block_id?: number | null;
}

export interface LocationCheck {
  ok: boolean;
  reason?: string;
}

/**
 * Can this evidence card ask for the archival context?
 *
 * A block id alone is not enough: the backend verifies that the block belongs
 * to the requested document, so a card without a document id must not trigger a
 * (possibly cross-document) lookup.
 */
export function checkEvidenceLocation(location: EvidenceLocation): LocationCheck {
  const blockId = location.block_id;
  const documentId = location.document_id;
  if (!Number.isInteger(blockId) || (blockId as number) <= 0) {
    return { ok: false, reason: "该证据没有关联的档案文本块，无法显示原文上下文。" };
  }
  if (!Number.isInteger(documentId) || (documentId as number) <= 0) {
    return { ok: false, reason: "该证据缺少文档标识，无法核对区块归属，因此不读取原文。" };
  }
  return { ok: true };
}

/** Query string for `GET /api/blocks/{blockId}/context`. */
export function buildBlockContextQuery(documentId: number, window: number = 1): string {
  const safeWindow = Number.isFinite(window) ? Math.min(3, Math.max(0, Math.trunc(window))) : 1;
  return `document_id=${encodeURIComponent(String(documentId))}&window=${safeWindow}`;
}

export interface BlockTextSection {
  key: "raw" | "corrected";
  label: string;
  text: string;
}

/**
 * Transcription sections to display, in a fixed order, skipping empties.
 *
 * Only stored OCR transcriptions are returned; model-generated modern English
 * and Chinese translation are never part of the archival original and are not
 * requested from the API at all.
 */
export function blockTextSections(block?: BlockContextBlock | null): BlockTextSection[] {
  if (!block) return [];
  const sections: BlockTextSection[] = [];
  const raw = (block.raw_text ?? "").trim() ? String(block.raw_text) : "";
  const corrected = (block.corrected_text ?? "").trim() ? String(block.corrected_text) : "";
  if (raw) sections.push({ key: "raw", label: "原始 OCR 转录", text: raw });
  if (corrected) sections.push({ key: "corrected", label: "校订 OCR 转录", text: corrected });
  return sections;
}

/** Compact location string, showing only values the database actually has. */
export function formatBlockLocation(block?: BlockContextBlock | null, pageNumber?: number | null): string {
  if (!block) return "定位信息不可用";
  const parts: string[] = [];
  if (typeof pageNumber === "number" && pageNumber > 0) parts.push(`第 ${pageNumber} 页`);
  else parts.push("页码未记录");
  parts.push(`Block #${block.block_id}`);
  parts.push(`页内序号 ${block.block_index}`);
  if (block.block_label) parts.push(`类型 ${block.block_label}`);
  return parts.join(" · ");
}

/** Neighbour count summary used in the dialog header. */
export function neighbourSummary(context?: BlockContextResponse | null): string {
  if (!context) return "";
  return `同页前文 ${context.previous.length} 段 / 后文 ${context.next.length} 段`;
}
