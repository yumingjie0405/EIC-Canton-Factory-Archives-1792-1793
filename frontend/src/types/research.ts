/**
 * Stage 8 R2: persisted research history types.
 */
import {
  AnswerGeneration,
  RagEntityReference,
  RagRelationshipReference,
  RagSourceEvidence,
} from "./rag";

export type ResearchMode = "rag" | "agent";

export type ResearchStatusValue =
  | "completed"
  | "degraded"
  | "insufficient_evidence"
  | "failed";

export interface ResearchRecordSummary {
  id: number;
  mode: ResearchMode | string;
  question: string;
  status: ResearchStatusValue | string;
  evidence_sufficient: boolean;
  generation_mode?: string | null;
  failure_reason?: string | null;
  citation_valid?: boolean | null;
  source_count: number;
  elapsed_ms?: number | null;
  group_id?: number | null;
  group_name?: string | null;
  created_at: string;
}

export interface ResearchHistoryListResponse {
  items: ResearchRecordSummary[];
  total: number;
  page: number;
  page_size: number;
}

/** Saved answer plus the evidence snapshot taken when the run executed. */
export interface ResearchRecordDetail {
  id: number;
  mode: ResearchMode | string;
  question: string;
  answer: string;
  status: ResearchStatusValue | string;
  evidence_sufficient: boolean;
  generation: AnswerGeneration;
  intent?: Record<string, unknown> | null;
  sources: RagSourceEvidence[];
  entities: RagEntityReference[];
  relationships: RagRelationshipReference[];
  warnings: string[];
  elapsed_ms?: number | null;
  group_id?: number | null;
  group_name?: string | null;
  created_at: string;
  updated_at?: string | null;
}
