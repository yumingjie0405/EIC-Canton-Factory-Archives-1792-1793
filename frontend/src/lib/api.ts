import {
  Document,
  DocumentListResponse,
  Page,
  Block,
  BlockListResponse,
  Task,
  Export,
  ExportListResponse,
  RunOCRRequest,
  RunPolishRequest,
  ExportRequest,
  BlockUpdateRequest,
} from "@/types";
import {
  Assertion,
  CanonicalEntity,
  EntityCreateRequest,
  EntityDossier,
  EvidenceLocator,
  EvidenceSpan,
  HistoricalEvent,
  KnowledgeListResponse,
  WorkbenchOverview,
} from "@/types/knowledge";
import {
  RagAnswer,
  RagIndexJob,
  RagIndexStatus,
  RagSearchResponse,
} from "@/types/rag";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function fetchApi<T>(
  endpoint: string,
  options?: RequestInit,
): Promise<T> {
  const url = `${API_BASE}/api${endpoint}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const detail = typeof error.detail === "string" ? error.detail : undefined;
    // The V2 knowledge workbench needs its own schema (created by `alembic -c alembic.ini
    // upgrade heads` on a pgvector-enabled database). Until that has been done every V2
    // call fails with a bare "Internal server error"; say what is actually missing.
    if (response.status >= 500 && endpoint.startsWith("/v2/")) {
      throw new ApiError(
        response.status,
        "V2 知识库尚未启用：数据库缺少 V2 表（需要 pgvector 扩展，并执行 "
          + "`alembic -c alembic.ini upgrade heads`）。V1 功能（人物图谱 / 知识审核 / "
          + "研究流程 / 智能问答 / AI 研究员）不受影响。",
      );
    }
    throw new ApiError(
      response.status,
      error.error?.message || detail || response.statusText,
    );
  }

  return response.json();
}

function buildQuery(
  params: Record<string, string | number | undefined>,
): string {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== "") search.set(key, String(value));
  });
  return search.toString();
}

// Document API
export const documentApi = {
  upload: async (file: File): Promise<Document> => {
    const formData = new FormData();
    formData.append("file", file);

    const response = await fetch(`${API_BASE}/api/documents/upload`, {
      method: "POST",
      body: formData,
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new ApiError(
        response.status,
        error.error?.message || "Upload failed",
      );
    }

    return response.json();
  },

  list: (
    page = 1,
    pageSize = 20,
    archived = false,
  ): Promise<DocumentListResponse> =>
    fetchApi(
      `/documents?page=${page}&page_size=${pageSize}&archived=${archived}`,
    ),

  get: (id: number): Promise<Document> => fetchApi(`/documents/${id}`),

  restore: (id: number): Promise<void> =>
    fetchApi(`/documents/${id}/restore`, { method: "POST" }),

  delete: (id: number): Promise<void> =>
    fetchApi(`/documents/${id}`, { method: "DELETE" }),

  runOcr: (id: number, options?: RunOCRRequest): Promise<Task> =>
    fetchApi(`/documents/${id}/run-ocr`, {
      method: "POST",
      body: JSON.stringify(options || {}),
    }),

  getPages: (id: number): Promise<{ items: Page[]; total: number }> =>
    fetchApi(`/documents/${id}/pages`),

  runPolish: (id: number, options?: RunPolishRequest): Promise<Task> =>
    fetchApi(`/blocks/documents/${id}/run-polish`, {
      method: "POST",
      body: JSON.stringify(options || {}),
    }),
};

// Page API
export const pageApi = {
  get: (id: number): Promise<Page> => fetchApi(`/pages/${id}`),

  getBlocks: (id: number): Promise<BlockListResponse> =>
    fetchApi(`/pages/${id}/blocks`),

  getImageUrl: (id: number): string => `${API_BASE}/api/pages/${id}/image`,

  getVisualizationUrl: (id: number): string =>
    `${API_BASE}/api/pages/${id}/visualization`,

  runPolish: (id: number, options?: RunPolishRequest): Promise<Task> =>
    fetchApi(`/blocks/pages/${id}/run-polish`, {
      method: "POST",
      body: JSON.stringify(options || {}),
    }),
};

// Block API
export const blockApi = {
  get: (id: number): Promise<Block> => fetchApi(`/blocks/${id}`),

  update: (id: number, data: BlockUpdateRequest): Promise<Block> =>
    fetchApi(`/blocks/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  runPolish: (id: number, options?: RunPolishRequest): Promise<Task> =>
    fetchApi(`/blocks/${id}/run-polish`, {
      method: "POST",
      body: JSON.stringify(options || {}),
    }),
};

// Task API
export const taskApi = {
  get: (id: number): Promise<Task> => fetchApi(`/tasks/${id}`),

  poll: async (
    id: number,
    onProgress?: (task: Task) => void,
    interval = 5000,
  ): Promise<Task> => {
    return new Promise((resolve, reject) => {
      const check = async () => {
        try {
          const task = await taskApi.get(id);
          onProgress?.(task);

          if (task.status === "completed") {
            resolve(task);
          } else if (task.status === "failed") {
            reject(new Error(task.error_message || "Task failed"));
          } else {
            setTimeout(check, interval);
          }
        } catch (error) {
          reject(error);
        }
      };
      check();
    });
  },
};

// Export API
export const exportApi = {
  create: (documentId: number, format: ExportRequest): Promise<Export> =>
    fetchApi(`/exports/documents/${documentId}/export`, {
      method: "POST",
      body: JSON.stringify(format),
    }),

  list: (documentId: number): Promise<ExportListResponse> =>
    fetchApi(`/exports/documents/${documentId}/exports`),

  getDownloadUrl: (exportId: number): string =>
    `${API_BASE}/api/exports/${exportId}/download`,
};

// Knowledge API (v2)
export const knowledgeApi = {
  overview: (): Promise<WorkbenchOverview> =>
    fetchApi(`/v2/workbench/overview`),

  listEntities: (
    page = 1,
    pageSize = 20,
    q?: string,
  ): Promise<KnowledgeListResponse<CanonicalEntity>> =>
    fetchApi(`/v2/entities?${buildQuery({ page, page_size: pageSize, q })}`),

  getEntity: (id: number): Promise<EntityDossier> =>
    fetchApi(`/v2/entities/${id}`),

  createEntity: (data: EntityCreateRequest): Promise<CanonicalEntity> =>
    fetchApi(`/v2/entities`, { method: "POST", body: JSON.stringify(data) }),

  listEvents: (
    page = 1,
    pageSize = 20,
    q?: string,
  ): Promise<KnowledgeListResponse<HistoricalEvent>> =>
    fetchApi(`/v2/events?${buildQuery({ page, page_size: pageSize, q })}`),

  listEvidence: (
    page = 1,
    pageSize = 20,
  ): Promise<KnowledgeListResponse<EvidenceSpan>> =>
    fetchApi(`/v2/evidence-spans?${buildQuery({ page, page_size: pageSize })}`),

  getEvidenceLocator: (id: number): Promise<EvidenceLocator> =>
    fetchApi(`/v2/evidence-spans/${id}/locator`),

  listAssertions: (
    page = 1,
    pageSize = 20,
    subjectEntityId?: number,
  ): Promise<KnowledgeListResponse<Assertion>> =>
    fetchApi(
      `/v2/assertions?${buildQuery({ page, page_size: pageSize, subject_entity_id: subjectEntityId })}`,
    ),
};

// RAG API (v2)
export const ragApi = {
  documents: (): Promise<{ items: import("@/types/rag").IndexDocument[] }> =>
    fetchApi("/v2/rag/documents"),
  status: (documentId?: number): Promise<RagIndexStatus> =>
    fetchApi(`/v2/rag/status?${buildQuery({ document_id: documentId })}`),

  startIndex: (documentIds: number[]): Promise<RagIndexJob> =>
    fetchApi(`/v2/rag/index`, {
      method: "POST",
      body: JSON.stringify({ document_ids: documentIds }),
    }),

  getIndexJob: (taskId: string): Promise<RagIndexJob> =>
    fetchApi(`/v2/rag/index/${taskId}`),

  search: (
    query: string,
    topK = 10,
    documentId?: number,
  ): Promise<RagSearchResponse> =>
    fetchApi(`/v2/rag/search`, {
      method: "POST",
      body: JSON.stringify({
        query,
        top_k: topK,
        document_id: documentId ?? null,
      }),
    }),

  ask: (question: string, topK = 8, documentId?: number): Promise<RagAnswer> =>
    fetchApi(`/v2/rag/questions`, {
      method: "POST",
      body: JSON.stringify({
        question,
        top_k: topK,
        document_id: documentId ?? null,
      }),
    }),
};

export { ApiError };
