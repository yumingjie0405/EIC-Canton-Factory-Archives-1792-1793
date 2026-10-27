
/**
 * Stage 6: Graph RAG Frontend Types
 */

export interface QueryIntentAnalysis {
  raw_query: string;
  query_type: "factual" | "relational" | "historical_analysis";
  entities: string[];
  time_filter?: number | null;
  spatial_filter?: string | null;
  search_keywords: string[];
}

export interface RagEntityReference {
  id: number;
  display_name: string;
  primary_role?: string | null;
  canonical_name_zh?: string | null;
  canonical_name_en?: string | null;
  organization?: string | null;
  identity_status: string;
  external_authorities_count: number;
  external_records: string[];
}

export interface RagRelationshipReference {
  source_person_name: string;
  target_person_name: string;
  relation_type: string;
  time_description?: string | null;
  confidence: number;
  claim_status: string;
  review_status: string;
  evidence_quote?: string | null;
}

export interface RagSourceEvidence {
  source_id: string; // e.g. "证据 #1"
  document_id?: number | null;
  document_name: string;
  page_number?: number | null;
  block_id?: number | null;
  quote_text: string;
  relevance_score: number;
  matched_entities: string[];
  evidence_type?: "primary_archival" | "relationship_metadata" | "external_scholarship";
  temporal_coverage?: string | null;
  temporal_provenance?: string | null;
}

export interface RagQueryRequest {
  /** Document group to search; resolved server-side, unknown groups are rejected. */
  group_id?: number;
  question: string;
  include_graph?: boolean;
  time_filter?: number | null;
  top_k?: number;
}

/** Result of verifying inline "[证据 #N]" markers against retrieved evidence. */
export interface CitationIntegrity {
  valid: boolean;
  cited_ids: number[];
  invalid_ids: number[];
  available_count: number;
}

/** Provenance of the answer text itself (model synthesis vs offline fallback). */
export interface AnswerGeneration {
  mode: "model" | "deterministic_fallback" | "refused_no_evidence" | string;
  degraded: boolean;
  model_name?: string | null;
  failure_reason?: string | null;
  failure_message?: string | null;
  failure_detail?: string | null;
  citation_check?: CitationIntegrity | null;
}

/** Whether the finished run was persisted, and under which record id. */
export interface HistoryLink {
  saved: boolean;
  record_id?: number | null;
  mode?: string | null;
  error?: string | null;
}

export interface RagQueryResponse {
  group_id?: number | null;
  group_name?: string | null;
  question: string;
  answer: string;
  evidence_sufficient: boolean;
  intent: QueryIntentAnalysis;
  entities: RagEntityReference[];
  relationships: RagRelationshipReference[];
  sources: RagSourceEvidence[];
  execution_time_ms: number;
  generation?: AnswerGeneration;
  history?: HistoryLink;
}

export interface RagEntityContextResponse {
  entity: RagEntityReference;
  neighbors: RagEntityReference[];
  relationships: RagRelationshipReference[];
  evidences: RagSourceEvidence[];

}
// ---- V2 knowledge workbench ??????????????????????
export interface IndexDocument {
  approved_chunks?: number;
  current_chunks?: number;
  corpus_id?: number | null;
  id: number;
  title: string;
  page_count: number;
  ocr_pages: number;
  eligible: number;
  indexed: number;
  status: string;
}
export interface RagIndexStatus {
  eligible: number;
  indexed: number;
  pending: number;
  embedding_model: string;
  vector_pending?: number;
}

export interface RagIndexJob {
  task_id: string;
  state: string;
  info?: {
    documents_done?: number;
    documents_total?: number;
    done?: number;
    total?: number;
    error?: string;
    indexed?: number;
    vector_failed?: number;
  } | null;
}

export interface RagSearchHit {
  chunk_id: number;
  block_id: number;
  document_id: number;
  document_title: string;
  page_number: number;
  content: string;
  score: number;
  viewer_path: string;
}

export interface RagSearchResponse {
  query: string;
  items: RagSearchHit[];
}

export interface RagCitation extends RagSearchHit {
  index: number;
  cited: boolean;
}

export interface RagAnswer {
  question: string;
  answer: string;
  insufficient_evidence: boolean;
  citations: RagCitation[];
  model: string;
}
