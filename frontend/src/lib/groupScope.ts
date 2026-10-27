/**
 * Pure helpers for document groups and group-scoped research.
 *
 * Kept free of React and `fetch` so they can be unit-tested with `node:test`.
 */
import { GroupImportStatusValue, ResearchGroup } from "../types/group";

export const IMPORT_STATUS_LABELS: Record<string, string> = {
  pending: "待处理",
  queued: "已排队",
  processing: "处理中",
  ready: "可检索",
  failed: "处理失败",
};

export function importStatusLabel(status?: string | null): string {
  if (!status) return "未知状态";
  return IMPORT_STATUS_LABELS[status] || status;
}

export function importStatusTone(status?: string | null): "ok" | "warn" | "bad" | "idle" {
  switch (status) {
    case "ready":
      return "ok";
    case "processing":
    case "queued":
      return "warn";
    case "failed":
      return "bad";
    default:
      return "idle";
  }
}

/** A group can only be used for research when at least one document is searchable. */
export function isGroupUsable(group?: Pick<ResearchGroup, "ready_count"> | null): boolean {
  return !!group && group.ready_count > 0;
}

/** Human readable scope label, e.g. `范围：Canton 1792（3/5 篇可检索）`. */
export function scopeLabel(group?: ResearchGroup | null): string {
  if (!group) return "范围：全库（未限定文档组）";
  return `范围：${group.name}（${group.ready_count}/${group.document_count} 篇可检索）`;
}

/** Warning shown before a run when the selected group cannot produce evidence. */
export function emptyGroupWarning(group?: ResearchGroup | null): string | null {
  if (!group) return null;
  if (group.document_count === 0) {
    return "该文档组内没有文档：在此范围内检索将返回空证据并拒答，不会回退到全库。";
  }
  if (group.ready_count === 0) {
    return "该文档组内尚无可检索的档案文本（OCR 未完成或已失败）：在此范围内检索将拒答。";
  }
  return null;
}

export interface ImportSummary {
  batchId: string;
  accepted: number;
  duplicates: number;
  failed: number;
  message: string;
}

/** Summarise an import batch response without hiding partial failures. */
export function importSummary(result: {
  batch_id: string;
  accepted_count: number;
  duplicate_count: number;
  failed_count: number;
}): ImportSummary {
  const parts = [`新增 ${result.accepted_count} 篇`];
  if (result.duplicate_count) parts.push(`重复跳过 ${result.duplicate_count} 篇`);
  if (result.failed_count) parts.push(`失败 ${result.failed_count} 篇`);
  return {
    batchId: result.batch_id,
    accepted: result.accepted_count,
    duplicates: result.duplicate_count,
    failed: result.failed_count,
    message: parts.join("，"),
  };
}

/** Parse a `?group=` query value; anything that is not a positive integer is ignored. */
export function parseGroupIdParam(value: string | null | undefined): number | null {
  if (!value) return null;
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1) return null;
  return parsed;
}

/** True when a batch still has documents that are not finished yet. */
export function batchIsPending(statusCounts: Record<string, number>): boolean {
  const pending = (statusCounts.pending || 0) + (statusCounts.queued || 0) + (statusCounts.processing || 0);
  return pending > 0;
}

/** Human readable batch progress, e.g. `可检索 2 / 共 3 篇`. */
export function batchProgressLabel(total: number, statusCounts: Record<string, number>): string {
  const ready = statusCounts.ready || 0;
  const failed = statusCounts.failed || 0;
  const parts = [`可检索 ${ready} / 共 ${total} 篇`];
  if (failed) parts.push(`失败 ${failed} 篇`);
  return parts.join("，");
}

/**
 * True when the requested scope is not in the known group list.
 *
 * An unknown `?group=` id must be reported, never silently rewritten to a
 * whole-archive search: that would answer a different question than the URL
 * asked for.
 */
export function isMissingScope(
  value: number | null | undefined,
  knownIds: number[],
  options: { isLoading?: boolean; hasError?: boolean } = {}
): boolean {
  if (value == null) return false;
  if (options.isLoading || options.hasError) return false;
  return !knownIds.includes(value);
}

/** Message shown when a requested scope does not exist (or is not accessible). */
export function missingScopeMessage(value: number | null | undefined): string {
  return `文档组 #${value ?? ""} 不存在或无权访问：检索请求会由服务端拒绝，不会退回到全库。`;
}

export function acceptedStatuses(): GroupImportStatusValue[] {
  return ["pending", "queued", "processing", "ready", "failed"];
}
