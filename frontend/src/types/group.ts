/**
 * Stage 8 R2.6: research group (document set) types.
 */

export type GroupImportStatusValue =
  | "pending"
  | "queued"
  | "processing"
  | "ready"
  | "failed";

export interface ResearchGroup {
  id: number;
  name: string;
  description?: string | null;
  auto_extract: boolean;
  document_count: number;
  ready_count: number;
  failed_count: number;
  created_at: string;
  updated_at?: string | null;
}

export interface ResearchGroupListResponse {
  items: ResearchGroup[];
  total: number;
  page: number;
  page_size: number;
}

export interface GroupMemberDocument {
  document_id: number;
  filename: string;
  original_filename?: string | null;
  file_type: string;
  file_size: number;
  page_count: number;
  block_count: number;
  document_status: string;
  import_status: GroupImportStatusValue | string;
  import_error?: string | null;
  last_batch_id?: string | null;
  added_at?: string | null;
}

/** Why automatic research will or will not run for a group. */
export interface AutoResearchStatus {
  global_enabled: boolean;
  group_enabled: boolean;
  model_configured: boolean;
  budget_blocks: number;
  budget_tokens?: number | null;
  model?: string | null;
  effective: boolean;
  reason: string;
  detail: string;
}

/** What happened to one document when auto research was attempted. */
export interface AutoExtractionOutcome {
  document_id?: number | null;
  started: boolean;
  reason: string;
  detail: string;
  run_id?: number | null;
  dispatch_state?: string | null;
  dispatch_error?: string | null;
  total_blocks: number;
}

export interface ResearchGroupDetailResponse {
  group: ResearchGroup;
  documents: GroupMemberDocument[];
  warning?: string | null;
  auto_research?: AutoResearchStatus | null;
}

export interface GroupMembershipChangeResponse {
  added: number[];
  duplicates: number[];
  missing: number[];
  group?: ResearchGroup | null;
  auto_research: AutoExtractionOutcome[];
}

export interface ImportBatchItem {
  filename: string;
  document_id?: number | null;
  duplicate: boolean;
  created_document: boolean;
  import_status?: GroupImportStatusValue | string | null;
  error?: string | null;
  auto_research?: AutoExtractionOutcome | null;
}

export interface ImportBatchResponse {
  batch_id: string;
  group_id: number;
  accepted_count: number;
  duplicate_count: number;
  failed_count: number;
  items: ImportBatchItem[];
  auto_research: AutoExtractionOutcome[];
}

export interface ImportBatchStatusResponse {
  batch_id: string;
  group_id: number;
  total: number;
  status_counts: Record<string, number>;
  items: ImportBatchItem[];
}
