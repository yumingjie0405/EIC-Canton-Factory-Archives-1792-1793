/**
 * Knowledge Graph, Social Network Analysis (SNA), and Disambiguation Review TypeScript definitions.
 * Strictly aligned with backend Pydantic schemas.
 */

export type IdentityStatus = "provisional" | "accepted" | "uncertain" | "contested" | "rejected";
export type ReviewStatusKG = "unreviewed" | "in_review" | "reviewed";
export type ResolutionStatus = "unresolved" | "candidate" | "resolved" | "uncertain" | "contested" | "rejected";
export type RelationType =
  | "commercial"
  | "diplomatic"
  | "guarantee"
  | "debt"
  | "correspondence"
  | "kinship"
  | "accompaniment"
  | "employment"
  | "translation"
  | "other";
export type ClaimStatus = "asserted" | "accepted" | "uncertain" | "contested" | "rejected";
export type AuthoritySource = "CBDB" | "IOR" | "Wikidata" | "VIAF" | "Liang_Canton" | "Peng_Commerce" | "Other";

export interface ExternalAuthority {
  id: number;
  person_id: number;
  authority_source: AuthoritySource;
  external_id?: string;
  external_url?: string;
  record_title?: string;
  historical_notes?: string;
  match_confidence?: number;
  review_status: ReviewStatusKG;
  claim_status: ClaimStatus;
  accessed_at?: string;
  created_at: string;
}

export interface Person {
  id: number;
  canonical_name_en?: string;
  canonical_name_zh?: string;
  display_name: string;
  aliases?: string[] | Record<string, any>;
  primary_role?: string;
  role_source?: string;
  role_category?: string;
  organization?: string;
  birth_year?: number;
  death_year?: number;
  active_start?: string;
  active_end?: string;
  base_location?: string;
  biographical_notes?: string;
  identity_status: IdentityStatus;
  review_status: ReviewStatusKG;
  created_at: string;
  updated_at: string;
}

export interface PersonDetail extends Person {
  external_authorities?: ExternalAuthority[];
}

export interface PersonListResponse {
  items: Person[];
  total: number;
}

export interface PersonMention {
  id: number;
  block_id: number;
  raw_text: string;
  normalized_text?: string;
  mention_type: string;
  temporal_year?: number;
  spatial_location?: string;
  confidence?: number;
  resolution_status: ResolutionStatus;
  person_id?: number;
  candidate_person_ids?: number[];
  review_status: ReviewStatusKG;
  created_at: string;
}

export interface PersonMentionListResponse {
  items: PersonMention[];
  total: number;
}

export interface CandidateMatch {
  person_id: number;
  display_name: string;
  canonical_name_en?: string;
  canonical_name_zh?: string;
  primary_role?: string;
  organization?: string;
  birth_year?: number;
  death_year?: number;
  active_start?: string;
  active_end?: string;
  total_score: number;
  name_score: number;
  temporal_score: number;
  spatial_score: number;
  role_score: number;
  context_score: number;
  authority_score: number;
  fatal_conflict: boolean;
  conflict_reasons: string[];
}

export interface MentionDisambiguationEvaluation {
  mention_id: number;
  raw_text: string;
  normalized_text?: string;
  temporal_year?: number;
  spatial_location?: string;
  current_resolution_status: string;
  candidates_count: number;
  candidates: CandidateMatch[];
}

export interface RelationshipEvidence {
  id: number;
  relationship_id: number;
  block_id: number;
  evidence_role: string;
  quote_text?: string;
  created_at: string;
}

export interface Relationship {
  id: number;
  source_person_id: number;
  target_person_id: number;
  relation_type: RelationType;
  relation_label?: string;
  directionality: "directed" | "undirected";
  weight?: number;
  valid_from?: string;
  valid_to?: string;
  time_description?: string;
  spatial_location?: string;
  evidence_summary?: string;
  confidence?: number;
  review_status: ReviewStatusKG;
  claim_status: ClaimStatus;
  created_at: string;
  updated_at: string;
  evidences?: RelationshipEvidence[];
}

export interface RelationshipListResponse {
  items: Relationship[];
  total: number;
}

export interface SNAGraphFilterParams {
  relation_types?: RelationType[];
  period_start?: string;
  period_end?: string;
  include_unknown_dates?: boolean;
  min_confidence?: number;
  include_provisional?: boolean;
  include_isolates?: boolean;
  directed?: boolean;
  group_id?: number;
  view?: GraphView;
}

/** Official = accepted claims; candidates = not-yet-accepted; all = everything extracted. */
export type GraphView = "all" | "official" | "candidates";

/** Data-derived extraction/coverage state of one document group. */
export type GroupGraphState =
  | "no_documents"
  | "no_text"
  | "not_extracted"
  | "queued_extraction"
  | "extracting"
  | "paused_extraction"
  | "extraction_failed"
  | "partial_extraction"
  | "extracted_empty"
  | "ready";

/** One row of the persistent extraction ledger (`kg_extraction_runs`). */
export interface KGExtractionRunStatus {
  run_id: number;
  scope_type: string;
  scope_id: number;
  group_id?: number | null;
  document_id?: number | null;
  status: string;
  model?: string | null;
  is_mock_model?: boolean;
  trigger?: string;
  dispatch_state?: string;
  dispatch_attempts?: number;
  dispatch_error?: string | null;
  budget_source?: string | null;
  reset_at?: string | null;
  reset_reason?: string | null;
  total_blocks: number;
  processed_blocks: number;
  skipped_blocks: number;
  failed_blocks: number;
  attempted_blocks: number;
  progress_percent: number;
  tokens_used: number;
  budget_blocks?: number | null;
  budget_tokens?: number | null;
  pause_reason?: string | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface KGExtractionRunsSummary {
  active_run?: KGExtractionRunStatus | null;
  paused_run?: KGExtractionRunStatus | null;
  last_finished_run?: KGExtractionRunStatus | null;
  failed_run?: KGExtractionRunStatus | null;
  primary_run?: KGExtractionRunStatus | null;
  recent_runs: KGExtractionRunStatus[];
}

/** Provenance of the stored records (mock runs are never presented as evidence). */
export interface GraphDataTrust {
  mock_derived: boolean;
  mock_run_ids: number[];
  models: string[];
  note: string;
}

export interface GraphFacetCount {
  value: string;
  count: number;
}

export interface GraphFacetYear {
  year: number;
  count: number;
}

export interface GraphFacets {
  group_id: number;
  group_name: string;
  view: GraphView;
  relation_types: GraphFacetCount[];
  dated_years: GraphFacetYear[];
  year_range?: { min: number; max: number } | null;
  undated_relationship_count: number;
  total_relationship_count: number;
  time_filter_available: boolean;
  time_filter_reason?: string | null;
  include_unknown_dates: boolean;
  mention_years: GraphFacetYear[];
  mention_year_note?: string | null;
  scope_person_count: number;
  data_trust?: GraphDataTrust | null;
}

export interface GroupGraphScope {
  group_id: number;
  group_name: string;
  document_count: number;
  block_count: number;
  blocks_with_mentions: number;
  blocks_with_extraction: number;
  mention_count: number;
  pending_mention_reviews: number;
  person_count: number;
  relationship_count_official: number;
  relationship_count_candidates: number;
  extraction_state: GroupGraphState;
  state_message: string;
  task_tracking_available: boolean;
  extraction_run?: KGExtractionRunStatus | null;
  extraction_runs?: KGExtractionRunsSummary | null;
  data_trust?: GraphDataTrust | null;
  auto_research?: {
    global_enabled: boolean;
    group_enabled: boolean;
    model_configured: boolean;
    budget_blocks: number;
    budget_tokens?: number | null;
    model?: string | null;
    effective: boolean;
    reason: string;
    detail: string;
  } | null;
}

/** One category of the cleanup preview: what would be removed and what is protected. */
export interface ExtractionResetResult {
  dry_run: boolean;
  scope: {
    group_id?: number | null;
    group_name?: string | null;
    document_id?: number | null;
    document_name?: string | null;
    document_count: number;
    block_count: number;
  };
  database_name: string;
  target_runs: {
    run_id: number;
    status: string;
    model?: string | null;
    is_mock_model: boolean;
    succeeded_blocks_in_scope: number;
    windowed_mention_blocks: number;
    windowed_evidence_blocks: number;
    budget_blocks?: number | null;
    started_at?: string | null;
    finished_at?: string | null;
  }[];
  counts: Record<string, number>;
  relationships_protected: {
    id: number;
    reason: string;
    deletable_evidence?: number;
    kept_evidence?: number;
  }[];
  /** Relationships kept as claims, but whose mock evidence would still be removed. */
  relationships_partially_cleaned: number[];
  persons_protected: { id: number; reason: string }[];
  shared_documents: number[];
  notes: string[];
  deleted: Record<string, number>;
  reset_at?: string | null;
}

export interface GroupGraphPerson {
  id: number;
  display_name: string;
  canonical_name_en?: string;
  canonical_name_zh?: string;
  primary_role?: string;
  organization?: string;
  identity_status: IdentityStatus;
  /** model_extracted | human_verified | heuristic | unknown (absent = no role recorded) */
  role_source?: string;
  /** Normalised category used for colouring; 'unknown' when no role is recorded */
  role_category?: string;
}

export interface GroupGraphRelationship {
  id: number;
  source_person_id: number;
  target_person_id: number;
  relation_type: string;
  relation_label?: string;
  directionality?: "directed" | "undirected";
  claim_status: ClaimStatus;
  review_status: ReviewStatusKG;
  confidence?: number;
  time_description?: string;
  evidence_count: number;
  /** Stored in-group evidence rows (>= evidence_count, which counts distinct blocks). */
  evidence_rows?: number;
  /** Distinct quote texts behind this claim inside the group. */
  distinct_quotes?: number;
  verified_evidence_count?: number;
  synthetic_evidence_count?: number;
  unknown_evidence_count?: number;
  evidence_block_ids: number[];
  /** Origin of the supporting evidence; independent of claim_status. */
  source_kind?: string;
  source_kind_label?: string;
  source_kinds?: Record<string, number>;
  synthetic?: boolean;
}

export interface GroupGraphOverview {
  group_id: number;
  group_name: string;
  view: GraphView;
  persons: GroupGraphPerson[];
  relationships: GroupGraphRelationship[];
  scope_person_count: number;
  scope_relationship_count: number;
  /** Persons per normalised role category in this subgraph (only real ones). */
  role_counts?: Record<string, number>;
  role_known_count?: number;
  role_unknown_count?: number;
  role_source_counts?: Record<string, number>;
  /** In-group evidence rows behind every edge in this subgraph. */
  evidence_rows_total?: number;
  verified_evidence_rows?: number;
  synthetic_evidence_rows?: number;
  unknown_evidence_rows?: number;
  evidence_note?: string;
}

export interface GroupEvidenceItem {
  evidence_id: number;
  document_id: number;
  document_name: string;
  page_number: number;
  block_id: number;
  block_index: number;
  block_label?: string;
  quote_text?: string;
  block_text?: string;
  evidence_role: string;
  source_kind?: string;
  source_kind_label?: string;
  extraction_run_id?: number | null;
  synthetic?: boolean;
}

export interface GroupRelationshipEvidence {
  group_id: number;
  relationship_id: number;
  source_person_id: number;
  target_person_id: number;
  relation_type: string;
  claim_status: string;
  review_status: string;
  items: GroupEvidenceItem[];
  out_of_group_evidence_count: number;
  /** Total in-group evidence rows; `items` may be capped by the API. */
  in_group_evidence_count?: number;
  /** Distinct in-group blocks (independent archival locators). */
  distinct_block_count?: number;
  distinct_quote_count?: number;
  source_kind_counts?: Record<string, number>;
  verified_evidence_count?: number;
  synthetic_evidence_count?: number;
  unknown_evidence_count?: number;
  truncated?: boolean;
}

export interface GroupPersonMentionItem {
  mention_id: number;
  raw_text: string;
  normalized_text?: string;
  mention_type: string;
  resolution_status: string;
  review_status: string;
  temporal_year?: number;
  document_id: number;
  document_name: string;
  page_number: number;
  block_id: number;
  block_index: number;
  evidence_text?: string;
  /** False when the mention merely shares the name and is not linked to this person. */
  linked?: boolean;
}

export interface GroupPersonMentions {
  group_id: number;
  person_id: number;
  display_name: string;
  identity_status: string;
  items: GroupPersonMentionItem[];
  /** Total in-group mentions; `items` may be capped by the API. */
  in_group_mention_count?: number;
  /** Same normalized name, identity link not yet confirmed. */
  unlinked_name_match_count?: number;
  unlinked_items?: GroupPersonMentionItem[];
  truncated?: boolean;
}

export interface SNANetworkSummary {
  node_count: number;
  edge_count: number;
  aggregated_edge_count: number;
  density: number;
  average_degree: number;
  average_in_degree?: number;
  average_out_degree?: number;
  connected_components_count: number;
  component_sizes: number[];
  average_clustering_coefficient: number;
  isolated_nodes_count: number;
  filter_parameters: Record<string, any>;
  algorithm_version: string;
  data_limits_note: string;
  group_id?: number;
  group_name?: string;
  view?: GraphView;
  scope_document_count?: number;
  scope_block_count?: number;
  edge_source_composition?: Record<string, number>;
  verified_edge_count?: number;
  synthetic_edge_count?: number;
  unknown_edge_count?: number;
  metrics_basis?: string;
}

export interface SNAPersonMetrics {
  person_id: number;
  display_name: string;
  canonical_name_en?: string;
  canonical_name_zh?: string;
  primary_role?: string;
  organization?: string;
  in_network: boolean;
  degree: number;
  in_degree: number;
  out_degree: number;
  degree_centrality: number;
  betweenness_centrality: number;
  closeness_centrality: number;
  pagerank: number;
  clustering_coefficient: number;
  community_id?: string;
  rank_degree?: number;
  rank_betweenness?: number;
  rank_closeness?: number;
  rank_pagerank?: number;
  note?: string;
}

export interface SNAPersonNeighborEvidence {
  id: number;
  relationship_id: number;
  block_id: number;
  quote_text?: string;
  evidence_role: string;
}

export interface SNAPersonNeighborItem {
  person_id: number;
  display_name: string;
  canonical_name_en?: string;
  canonical_name_zh?: string;
  primary_role?: string;
  organization?: string;
  direction: "out" | "in" | "mutual";
  relation_count: number;
  relation_types: string[];
  evidence_count: number;
  evidence_block_ids: number[];
  evidences: SNAPersonNeighborEvidence[];
}

export interface SNAPersonNeighborsResponse {
  person_id: number;
  display_name: string;
  neighbor_count: number;
  neighbors: SNAPersonNeighborItem[];
}

export interface MergePreviewConflict {
  field: string;
  primary_value: any;
  secondary_value: any;
  recommendation: string;
}

export interface MergePreviewResponse {
  primary_person_id: number;
  secondary_person_id: number;
  mentions_to_move_count: number;
  out_relationships_to_move_count: number;
  in_relationships_to_move_count: number;
  authorities_to_move_count: number;
  has_conflicts: boolean;
  conflicts: MergePreviewConflict[];
  safe_to_merge: boolean;
}

export interface AuditRecord {
  id: number;
  operation_type: string;
  primary_person_id: number;
  secondary_person_id?: number;
  snapshot_details: Record<string, any>;
  reason?: string;
  operator?: string;
  is_reverted: boolean;
  created_at: string;
}

// Frontend Interactive Graph Render Types
export interface GraphNode {
  id: number;
  label: string;
  role?: string;
  organization?: string;
  status: IdentityStatus;
  degree?: number;
  pagerank?: number;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  radius?: number;
  color?: string;
}

export interface GraphLink {
  id: string;
  source: number | GraphNode;
  target: number | GraphNode;
  relationType: RelationType | string;
  direction: "out" | "in" | "mutual" | "directed" | "undirected";
  relationCount: number;
  evidenceCount: number;
  evidenceBlockIds: number[];
  /** Claim status of the underlying relationship; non-accepted edges are dashed. */
  claimStatus?: string;
  color?: string;
}
