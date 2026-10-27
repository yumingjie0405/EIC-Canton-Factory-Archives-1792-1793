/**
 * Read-only archival block context API client.
 */
import { BlockContextResponse } from "@/types/blockContext";
import { buildBlockContextQuery } from "@/lib/blockContext";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export class BlockContextError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "BlockContextError";
  }
}

export function blockContextUrl(blockId: number, documentId: number, window: number = 1): string {
  return `${API_BASE}/api/blocks/${blockId}/context?${buildBlockContextQuery(documentId, window)}`;
}

/** Fetch the cited block and its same-page neighbours. Read-only. */
export async function fetchBlockContext(
  blockId: number,
  documentId: number,
  window: number = 1
): Promise<BlockContextResponse> {
  const response = await fetch(blockContextUrl(blockId, documentId, window));

  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    const detail =
      payload?.detail ||
      payload?.error?.message ||
      `无法读取档案原文（HTTP ${response.status}）`;
    throw new BlockContextError(response.status, detail);
  }

  return response.json();
}
