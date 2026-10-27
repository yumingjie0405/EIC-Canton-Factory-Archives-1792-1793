/**
 * Per-file rules for group batch import.
 *
 * Large Word exports from this platform embed page images and can reach hundreds
 * of megabytes. A single multipart request carrying several of them fails in the
 * browser (the whole body is buffered) before it reaches the server, which is why
 * the importer uploads one file per request and reports each outcome separately.
 */

/** Must stay in sync with backend `settings.max_upload_file_bytes`. */
export const MAX_IMPORT_FILE_MB = 512;
export const MAX_IMPORT_FILE_BYTES = MAX_IMPORT_FILE_MB * 1024 * 1024;

export interface ImportFileResult {
  name: string;
  ok: boolean;
  message: string;
}

export function isFileTooLarge(bytes: number, limitBytes: number = MAX_IMPORT_FILE_BYTES): boolean {
  return bytes > limitBytes;
}

export function megabytes(bytes: number): number {
  return Math.round((bytes / (1024 * 1024)) * 10) / 10;
}

export function oversizedFileMessage(
  name: string,
  bytes: number,
  limitMb: number = MAX_IMPORT_FILE_MB
): string {
  return `${name} 为 ${megabytes(bytes)} MB，超过单文件上限 ${limitMb} MB，未上传。请拆分后再导入。`;
}

/**
 * Turns an import error into a message that names the real cause, so a failed
 * upload never looks like a silent success or a generic "导入失败".
 */
export function groupImportErrorMessage(error: any): string {
  if (!error) return "导入失败（未返回原因）。";
  const status = typeof error.status === "number" ? error.status : null;
  const raw = typeof error.message === "string" ? error.message : String(error);
  if (status === 413) {
    return raw || `文件超过单文件上限 ${MAX_IMPORT_FILE_MB} MB。`;
  }
  if (/failed to fetch|load failed|networkerror|network error/i.test(raw)) {
    return `请求未能发送到服务端（文件过大或后端不可用）：请确认单个文件不超过 ${MAX_IMPORT_FILE_MB} MB，并检查后端服务是否运行。`;
  }
  return raw || "导入失败。";
}

/** One-line summary for a batch of per-file outcomes. */
export function summarizeImportResults(results: ImportFileResult[]): string {
  if (results.length === 0) return "";
  const failed = results.filter((result) => !result.ok).length;
  if (failed === 0) {
    return `已导入 ${results.length} 个文件。`;
  }
  return `${results.length - failed} 个文件成功，${failed} 个失败（原因见逐文件结果）。`;
}

export interface ImportResponseLike {
  accepted_count: number;
  duplicate_count: number;
  failed_count: number;
  items?: { error?: string | null }[];
}

/**
 * Classifies one import response for the per-file report.
 *
 * A duplicate is **not** a failure: the same bytes are already in the group, so
 * nothing was added. Only an explicit failure count (or a file that was neither
 * accepted nor recognised as duplicate) is reported as failed.
 */
export function classifyImportResponse(result: ImportResponseLike): { ok: boolean; message: string } {
  if (result.failed_count > 0) {
    const reason = (result.items || []).map((item) => item.error).find(Boolean);
    return { ok: false, message: reason || "导入失败（服务端未返回原因）。" };
  }
  if (result.accepted_count === 0 && result.duplicate_count > 0) {
    return { ok: true, message: "已存在（按内容哈希去重，未新增成员）。" };
  }
  if (result.accepted_count === 0) {
    return { ok: false, message: "未导入任何内容（服务端未受理该文件）。" };
  }
  const parts = [`新增 ${result.accepted_count} 篇`];
  if (result.duplicate_count) parts.push(`重复跳过 ${result.duplicate_count} 篇`);
  return { ok: true, message: parts.join("，") };
}
