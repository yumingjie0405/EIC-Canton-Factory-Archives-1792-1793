/**
 * Knowledge Graph, SNA, and Disambiguation API client service.
 */
import {
  AuditRecord,
  GraphFacets,
  GroupGraphOverview,
  GroupGraphScope,
  GroupPersonMentions,
  GroupRelationshipEvidence,
  KGExtractionRunStatus,
  KGExtractionRunsSummary,
  ExtractionResetResult,
  MergePreviewResponse,
  MentionDisambiguationEvaluation,
  Person,
  PersonDetail,
  PersonListResponse,
  PersonMentionListResponse,
  Relationship,
  RelationshipListResponse,
  SNAGraphFilterParams,
  GraphView,
  SNANetworkSummary,
  SNAPersonMetrics,
  SNAPersonNeighborsResponse,
  ReviewStatusKG,
  ClaimStatus,
} from "@/types/kg";
import {
  BatchLinkPreview,
  BatchLinkResult,
  MentionClusterList,
  RelationshipReviewList,
  RelationshipReviewResult,
  SourceKind,
  SnapshotCompare,
  SnapshotDetail,
  SnapshotSummary,
} from "@/types/kgWorkflow";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

class ApiError extends Error {
  constructor(
    public status: number,
    message: string
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function fetchKgApi<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const url = `${API_BASE}/api/kg${endpoint}`;
  const response = await fetch(url, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    const message =
      errorData.detail ||
      errorData.error?.message ||
      errorData.message ||
      `Request failed with status ${response.status}`;
    throw new ApiError(response.status, message);
  }

  return response.json();
}

function buildQueryString(params: Record<string, any>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      if (Array.isArray(value)) {
        value.forEach((v) => query.append(key, String(v)));
      } else {
        query.append(key, String(value));
      }
    }
  }
  const str = query.toString();
  return str ? `?${str}` : "";
}

export const kgApi = {
  // ==========================================
  // 1. Person Entities
  // ==========================================
  listPersons: (params?: {
    q?: string;
    identity_status?: string;
    primary_role?: string;
    group_id?: number;
    skip?: number;
    limit?: number;
  }): Promise<PersonListResponse> =>
    fetchKgApi(`/persons${buildQueryString(params || {})}`),

  getPersonDetail: (personId: number): Promise<PersonDetail> =>
    fetchKgApi(`/persons/${personId}`),

  // ==========================================
  // 2. SNA Network Analysis & Ego-Networks
  // ==========================================
  getNetworkSummary: (filters?: SNAGraphFilterParams): Promise<SNANetworkSummary> =>
    fetchKgApi(`/sna/summary${buildQueryString(filters || {})}`),

  getPersonMetrics: (
    personId: number,
    filters?: SNAGraphFilterParams
  ): Promise<SNAPersonMetrics> =>
    fetchKgApi(`/sna/persons/${personId}/metrics${buildQueryString(filters || {})}`),

  getPersonNeighbors: (
    personId: number,
    filters?: SNAGraphFilterParams
  ): Promise<SNAPersonNeighborsResponse> =>
    fetchKgApi(`/sna/persons/${personId}/neighbors${buildQueryString(filters || {})}`),

  createSnapshot: (payload: {
    run_id?: string;
    graph_scope?: string;
    filters?: SNAGraphFilterParams;
  }): Promise<any> =>
    fetchKgApi("/sna/snapshot", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listSnapshots: (limit = 20): Promise<any[]> =>
    fetchKgApi(`/sna/snapshots?limit=${limit}`),

  getSnapshotRunMetrics: (runId: string): Promise<any[]> =>
    fetchKgApi(`/sna/snapshots/${encodeURIComponent(runId)}`),

  // ==========================================
  // 3. Mention Disambiguation & Human Review
  // ==========================================
  listMentions: (params?: {
    block_id?: number;
    resolution_status?: string;
    skip?: number;
    limit?: number;
  }): Promise<PersonMentionListResponse> =>
    fetchKgApi(`/mentions${buildQueryString(params || {})}`),

  getMentionCandidates: (mentionId: number): Promise<MentionDisambiguationEvaluation> =>
    fetchKgApi(`/disambiguate/mention/${mentionId}/candidates`),

  confirmMentionResolution: (
    mentionId: number,
    payload: { person_id: number; reviewer?: string; reason?: string }
  ): Promise<any> =>
    fetchKgApi(`/disambiguate/mention/${mentionId}/confirm`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  rejectMentionCandidate: (
    mentionId: number,
    payload: { person_id: number; reviewer?: string; reason?: string }
  ): Promise<any> =>
    fetchKgApi(`/disambiguate/mention/${mentionId}/reject`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  deferMentionResolution: (
    mentionId: number,
    payload: { reviewer?: string; reason?: string }
  ): Promise<any> =>
    fetchKgApi(`/disambiguate/mention/${mentionId}/defer`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // ==========================================
  // 4. Entity Merging, Reversible Splitting & Audit
  // ==========================================
  previewMerge: (
    primaryId: number,
    secondaryId: number
  ): Promise<MergePreviewResponse> =>
    fetchKgApi(`/entities/merge-preview?primary_id=${primaryId}&secondary_id=${secondaryId}`),

  executeMerge: (payload: {
    primary_person_id: number;
    secondary_person_id: number;
    operator?: string;
    reason?: string;
  }): Promise<any> =>
    fetchKgApi("/entities/merge", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  splitMerge: (payload: {
    audit_id: number;
    operator?: string;
    reason?: string;
  }): Promise<any> =>
    fetchKgApi("/entities/split", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  listAuditLogs: (limit = 50): Promise<AuditRecord[]> =>
    fetchKgApi(`/audit-logs?limit=${limit}`),

  // ==========================================
  // 5. Historical Relationships & Review
  // ==========================================
  listRelationships: (params?: {
    relation_type?: string;
    group_id?: number;
    view?: GraphView;
    skip?: number;
    limit?: number;
  }): Promise<RelationshipListResponse> =>
    fetchKgApi(`/relationships${buildQueryString(params || {})}`),

  // ==========================================
  // 6. Group-scoped person graph (evidence-first)
  // ==========================================
  getGroupGraphScope: (groupId: number): Promise<GroupGraphScope> =>
    fetchKgApi(`/groups/${groupId}/graph/scope`),

  getGroupGraphOverview: (
    groupId: number,
    filters?: SNAGraphFilterParams
  ): Promise<GroupGraphOverview> =>
    fetchKgApi(
      `/groups/${groupId}/graph/overview${buildQueryString(filters || {})}`
    ),

  /** Filter metadata computed from the same subgraph as the overview. */
  getGroupGraphFacets: (
    groupId: number,
    filters?: SNAGraphFilterParams
  ): Promise<GraphFacets> =>
    fetchKgApi(`/groups/${groupId}/graph/facets${buildQueryString(filters || {})}`),

  /** Classified run history: live / paused / last finished are reported separately. */
  getGroupExtractionRuns: (groupId: number, limit = 10): Promise<KGExtractionRunsSummary> =>
    fetchKgApi(`/groups/${groupId}/extraction/runs?limit=${limit}`),

  /** Cleanup of mock-derived records. Dry-run by default; execution needs confirmation. */
  resetGroupExtraction: (
    groupId: number,
    payload: {
      dryRun?: boolean;
      runIds?: number[];
      confirm?: string;
      reason?: string;
    }
  ): Promise<ExtractionResetResult> =>
    fetchKgApi(`/groups/${groupId}/extraction/reset`, {
      method: "POST",
      body: JSON.stringify({
        dry_run: payload.dryRun ?? true,
        run_ids: payload.runIds ?? null,
        confirm: payload.confirm ?? null,
        reason: payload.reason ?? null,
      }),
    }),

  getGroupRelationshipEvidence: (
    groupId: number,
    relationshipId: number
  ): Promise<GroupRelationshipEvidence> =>
    fetchKgApi(`/groups/${groupId}/relationships/${relationshipId}/evidence`),

  getGroupPersonMentions: (
    groupId: number,
    personId: number
  ): Promise<GroupPersonMentions> =>
    fetchKgApi(`/groups/${groupId}/persons/${personId}/mentions`),

  // ==========================================
  // 6b. Extraction ledger (manual start, resumable, budgeted)
  // ==========================================
  startGroupExtraction: (
    groupId: number,
    options?: {
      documentId?: number;
      budgetBlocks?: number;
      budgetTokens?: number;
      maxConsecutiveFailures?: number;
    }
  ): Promise<KGExtractionRunStatus> =>
    fetchKgApi(
      `/groups/${groupId}/extraction${buildQueryString({
        document_id: options?.documentId,
        budget_blocks: options?.budgetBlocks,
        budget_tokens: options?.budgetTokens,
        max_consecutive_failures: options?.maxConsecutiveFailures,
      })}`,
      { method: "POST" }
    ),

  getGroupExtraction: (groupId: number): Promise<KGExtractionRunStatus | null> =>
    fetchKgApi(`/groups/${groupId}/extraction`),

  getExtractionRun: (runId: number): Promise<KGExtractionRunStatus> =>
    fetchKgApi(`/extraction/runs/${runId}`),

  pauseExtractionRun: (runId: number): Promise<KGExtractionRunStatus> =>
    fetchKgApi(`/extraction/runs/${runId}/pause`, { method: "POST" }),

  resumeExtractionRun: (
    runId: number,
    options?: { budgetBlocks?: number; budgetTokens?: number }
  ): Promise<KGExtractionRunStatus> =>
    fetchKgApi(
      `/extraction/runs/${runId}/resume${buildQueryString({
        budget_blocks: options?.budgetBlocks,
        budget_tokens: options?.budgetTokens,
      })}`,
      { method: "POST" }
    ),

  redispatchExtractionRun: (runId: number): Promise<KGExtractionRunStatus> =>
    fetchKgApi(`/extraction/runs/${runId}/dispatch`, { method: "POST" }),

  reviewRelationship: (
    relationshipId: number,
    payload: {
      review_status: ReviewStatusKG;
      claim_status?: ClaimStatus;
      reviewer?: string;
      evidence_notes?: string;
    }
  ): Promise<Relationship> =>
    fetchKgApi(`/relationships/${relationshipId}/review`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getKgStats: (): Promise<{
    total_mentions: number;
    total_persons: number;
    total_relationships: number;
  }> => fetchKgApi("/stats"),

  // ==========================================
  // 8. Review workflow (Stage 10)
  // ==========================================
  listGroupRelationships: (
    groupId: number,
    params?: {
      claim_status?: string;
      review_status?: string;
      relation_type?: string;
      source_kind?: SourceKind;
      has_date?: boolean;
      q?: string;
      skip?: number;
      limit?: number;
    }
  ): Promise<RelationshipReviewList> =>
    fetchKgApi(`/groups/${groupId}/relationships${buildQueryString(params || {})}`),

  /** Persist one review decision; provenance is returned unchanged. */
  reviewRelationshipClaim: (
    relationshipId: number,
    payload: {
      claim_status?: string;
      review_status?: string;
      evidence_note?: string;
      reason?: string;
      reviewer?: string;
    }
  ): Promise<RelationshipReviewResult> =>
    fetchKgApi(`/relationships/${relationshipId}/review`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getRelationshipReviewHistory: (
    relationshipId: number,
    limit = 50
  ): Promise<
    {
      id: number;
      operation_type: string;
      before?: Record<string, unknown> | null;
      after?: Record<string, unknown> | null;
      reason?: string | null;
      operator: string;
      is_reverted: boolean;
      created_at?: string | null;
    }[]
  > => fetchKgApi(`/relationships/${relationshipId}/review-history?limit=${limit}`),

  listMentionClusters: (
    groupId: number,
    params?: { only_unresolved?: boolean; source_kind?: SourceKind; limit_groups?: number }
  ): Promise<MentionClusterList> =>
    fetchKgApi(`/groups/${groupId}/mentions/clusters${buildQueryString(params || {})}`),

  previewMentionBatchLink: (
    groupId: number,
    payload: { normalized_text: string; person_id?: number; new_person_name?: string }
  ): Promise<BatchLinkPreview> =>
    fetchKgApi(`/groups/${groupId}/mentions/batch-link/preview`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  confirmMentionBatchLink: (
    groupId: number,
    payload: {
      normalized_text: string;
      confirm_token: string;
      person_id?: number;
      new_person_name?: string;
      reason?: string;
      operator?: string;
    }
  ): Promise<BatchLinkResult> =>
    fetchKgApi(`/groups/${groupId}/mentions/batch-link/confirm`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  revertMentionBatch: (batchId: string, reason?: string): Promise<{ batch_id: string; restored_mentions: number }> =>
    fetchKgApi(
      `/mentions/batches/${encodeURIComponent(batchId)}/revert${buildQueryString({ reason })}`,
      { method: "POST" }
    ),

  createGraphSnapshot: (payload: {
    group_id: number;
    run_id?: string;
    name?: string;
    note?: string;
    view?: string;
    filters?: Record<string, unknown>;
    created_by?: string;
  }): Promise<SnapshotDetail> =>
    fetchKgApi("/graph/snapshots", { method: "POST", body: JSON.stringify(payload) }),

  listGraphSnapshots: (groupId?: number, limit = 20): Promise<SnapshotSummary[]> =>
    fetchKgApi(`/graph/snapshots${buildQueryString({ group_id: groupId, limit })}`),

  getGraphSnapshot: (runId: string): Promise<SnapshotDetail> =>
    fetchKgApi(`/graph/snapshots/${encodeURIComponent(runId)}`),

  compareGraphSnapshots: (left: string, right: string): Promise<SnapshotCompare> =>
    fetchKgApi(`/graph/snapshots/compare${buildQueryString({ left, right })}`),
};
