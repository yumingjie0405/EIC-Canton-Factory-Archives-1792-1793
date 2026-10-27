/**
 * Stage 8 R2: saved research history API client.
 */
import {
  ResearchHistoryListResponse,
  ResearchMode,
  ResearchRecordDetail,
} from "@/types/research";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export class ResearchApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ResearchApiError";
  }
}

async function fetchResearchApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}/api/research${endpoint}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new ResearchApiError(
      response.status,
      errorData.detail || errorData.error?.message || `Research API request failed (${response.status})`
    );
  }

  return response.json();
}

export const researchApi = {
  listHistory: (
    page = 1,
    pageSize = 20,
    mode?: ResearchMode | null
  ): Promise<ResearchHistoryListResponse> => {
    const params = new URLSearchParams({
      page: String(page),
      page_size: String(pageSize),
    });
    if (mode) params.set("mode", mode);
    return fetchResearchApi<ResearchHistoryListResponse>(`/history?${params.toString()}`);
  },

  getRecord: (recordId: number): Promise<ResearchRecordDetail> =>
    fetchResearchApi<ResearchRecordDetail>(`/history/${recordId}`),
};
