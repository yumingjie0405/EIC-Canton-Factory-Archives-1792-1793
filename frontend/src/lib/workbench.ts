import {
  Annotation,
  Answer,
  Evidence,
  Extraction,
  Hit,
  Paged,
  Scope,
} from "@/types/workbench";
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
export const apiUrl = (path: string) => `${API_BASE}${path}`;
export async function request<T>(
  path: string,
  options?: RequestInit,
): Promise<T> {
  const response = await fetch(apiUrl(`/api/v2${path}`), {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    // The V2 workbench needs its own schema (pgvector + `alembic -c alembic.ini upgrade
    // heads`). Until that exists every call answers with a bare "Internal server error";
    // say what is actually missing instead of echoing an opaque message.
    if (response.status >= 500) {
      throw new Error(
        "V2 知识库尚未启用：数据库缺少 V2 表（需要 pgvector 扩展，并执行 "
          + "`alembic -c alembic.ini upgrade heads`）。V1 功能（人物图谱 / 知识审核 / "
          + "研究流程 / 智能问答 / AI 研究员）不受影响。",
      );
    }
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : Array.isArray(error.detail)
          ? error.detail
              .map((item: { msg?: string }) => item.msg || "参数无效")
              .join("；")
          : error.detail?.message ||
            error.error?.message ||
            `请求失败 (${response.status})`,
    );
  }
  return response.json();
}
const post = <T>(path: string, data: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(data) });
export const workbenchApi = {
  overview: () =>
    request<{
      pages: number;
      ocr_pages: number;
      unresolved_mentions: number;
      pending: number;
      annotations: number;
      documents: number;
      entities: number;
      events: number;
      recent_documents: import("@/types").Document[];
      tasks: import("@/types").Task[];
      extractions: Extraction[];
    }>("/workbench/overview"),
  annotations: (params: URLSearchParams) =>
    request<Paged<Annotation>>(`/workbench/annotations?${params}`),
  annotation: (id: number) =>
    request<Annotation>(`/workbench/annotations/${id}`),
  createEvidence: (data: unknown) =>
    post<Evidence>("/workbench/evidence", data),
  createAnnotation: (data: unknown) =>
    post<Annotation>("/workbench/annotations", data),
  review: (id: number, data: unknown) =>
    post<Annotation>(`/workbench/annotations/${id}/reviews`, data),
  extraction: (id: number) =>
    request<Extraction>(`/workbench/extractions/${id}`),
  extract: (id: number, data: unknown) =>
    post<Extraction>(`/workbench/documents/${id}/extractions`, data),
  retryExtraction: (id: number) =>
    post<Extraction>(`/workbench/extractions/${id}/retry`, {}),
  search: (query: string, scope: Scope) =>
    post<{ items: Hit[]; warnings: string[]; exhaustive: boolean }>(
      "/rag/search",
      { query, top_k: 30, ...scope },
    ),
  ask: (
    question: string,
    scope: Scope,
    ids: number[],
    history: Array<{ question: string; answer: string }> = [],
  ) =>
    post<Answer>("/rag/questions", {
      question,
      top_k: 8,
      ...scope,
      selected_chunk_ids: ids,
      history,
    }),
};
