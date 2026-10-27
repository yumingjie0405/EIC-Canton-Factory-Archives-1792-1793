/**
 * Stage 7: AI Agent Dynamic Workflow API Client
 */
import {
  AgentResearchRequest,
  AgentResearchResponse,
  AgentPlanResponse,
  ToolDefinition,
} from "@/types/agent";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export class AgentApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "AgentApiError";
  }
}

async function fetchAgentApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}/api/agent${endpoint}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new AgentApiError(
      response.status,
      errorData.detail || `Agent API request failed (${response.status})`
    );
  }

  return response.json();
}

/**
 * Execute multi-step autonomous historical research workflow.
 */
export async function runAgentResearch(
  request: AgentResearchRequest
): Promise<AgentResearchResponse> {
  return fetchAgentApi<AgentResearchResponse>("/research", {
    method: "POST",
    body: JSON.stringify(request),
  });
}

/**
 * Preview decomposed research sub-goals without executing full tool loop.
 */
export async function previewAgentPlan(
  question: string,
  timeFilter?: number | null
): Promise<AgentPlanResponse> {
  const params = new URLSearchParams({ question });
  if (timeFilter) {
    params.set("time_filter", timeFilter.toString());
  }
  return fetchAgentApi<AgentPlanResponse>(`/plan?${params.toString()}`, {
    method: "POST",
  });
}

/**
 * Get catalog of whitelisted tools available to the Agent.
 */
export async function fetchAgentTools(): Promise<ToolDefinition[]> {
  return fetchAgentApi<ToolDefinition[]>("/tools");
}
