/**
 * Research group API client (list/create/detail/members/import).
 */
import {
  GroupMembershipChangeResponse,
  ImportBatchResponse,
  ImportBatchStatusResponse,
  ResearchGroup,
  ResearchGroupDetailResponse,
  ResearchGroupListResponse,
} from "@/types/group";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export class GroupApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "GroupApiError";
  }
}

async function fetchGroupApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}/api/groups${endpoint}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new GroupApiError(
      response.status,
      errorData.detail || `Document group request failed (${response.status})`
    );
  }

  return response.json();
}

export const groupsApi = {
  list: (page = 1, pageSize = 50, q?: string): Promise<ResearchGroupListResponse> => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (q) params.set("q", q);
    return fetchGroupApi<ResearchGroupListResponse>(`?${params.toString()}`);
  },

  create: (name: string, description?: string): Promise<ResearchGroup> =>
    fetchGroupApi<ResearchGroup>("", {
      method: "POST",
      body: JSON.stringify({ name, description: description || null }),
    }),

  detail: (groupId: number, refresh = true): Promise<ResearchGroupDetailResponse> =>
    fetchGroupApi<ResearchGroupDetailResponse>(
      `/${groupId}?refresh=${refresh ? "true" : "false"}`
    ),

  rename: (groupId: number, name: string, description?: string): Promise<ResearchGroup> =>
    fetchGroupApi<ResearchGroup>(`/${groupId}`, {
      method: "PATCH",
      body: JSON.stringify({ name, description }),
    }),

  setAutoExtract: (groupId: number, autoExtract: boolean): Promise<ResearchGroup> =>
    fetchGroupApi<ResearchGroup>(`/${groupId}`, {
      method: "PATCH",
      body: JSON.stringify({ auto_extract: autoExtract }),
    }),

  remove: (groupId: number): Promise<{ message: string }> =>
    fetchGroupApi<{ message: string }>(`/${groupId}`, { method: "DELETE" }),

  attachDocuments: (
    groupId: number,
    documentIds: number[]
  ): Promise<GroupMembershipChangeResponse> =>
    fetchGroupApi<GroupMembershipChangeResponse>(`/${groupId}/documents`, {
      method: "POST",
      body: JSON.stringify({ document_ids: documentIds }),
    }),

  detachDocument: (groupId: number, documentId: number): Promise<{ message: string }> =>
    fetchGroupApi<{ message: string }>(`/${groupId}/documents/${documentId}`, {
      method: "DELETE",
    }),

  retryDocument: (groupId: number, documentId: number) =>
    fetchGroupApi(`/${groupId}/documents/${documentId}/retry`, { method: "POST" }),

  importFiles: async (
    groupId: number,
    files: File[],
    runOcr = true
  ): Promise<ImportBatchResponse> => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name));
    const response = await fetch(
      `${API_BASE}/api/groups/${groupId}/import?run_ocr=${runOcr ? "true" : "false"}`,
      { method: "POST", body: form }
    );
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      throw new GroupApiError(
        response.status,
        errorData.detail || `Import failed (${response.status})`
      );
    }
    return response.json();
  },

  batchStatus: (groupId: number, batchId: string): Promise<ImportBatchStatusResponse> =>
    fetchGroupApi<ImportBatchStatusResponse>(`/${groupId}/imports/${batchId}`),
};

export default groupsApi;
