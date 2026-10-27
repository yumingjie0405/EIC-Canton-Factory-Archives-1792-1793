/**
 * Graph RAG API client service.
 */
import {
  RagQueryRequest,
  RagQueryResponse,
  RagEntityContextResponse,
  QueryIntentAnalysis,
} from "@/types/rag";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

class RagApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "RagApiError";
  }
}

async function fetchRagApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}/api/rag${endpoint}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new RagApiError(
      response.status,
      errorData.detail || `Graph RAG API request failed (${response.status})`
    );
  }

  return response.json();
}

/**
 * Execute natural language research query against Graph RAG engine.
 */
export async function askGraphRag(request: RagQueryRequest): Promise<RagQueryResponse> {
  return fetchRagApi<RagQueryResponse>("/query", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

/**
 * Retrieve graph context and grounded archival citations for a specific historical person entity.
 */
export async function getEntityRagContext(entityId: number): Promise<RagEntityContextResponse> {
  return fetchRagApi<RagEntityContextResponse>(`/context/${entityId}`);
}

/**
 * Perform query understanding and intent analysis.
 */
export async function analyzeRagQuery(queryText: string): Promise<QueryIntentAnalysis> {
  return fetchRagApi<QueryIntentAnalysis>(`/analyze?query_text=${encodeURIComponent(queryText)}`, {
    method: "POST",
  });
}
