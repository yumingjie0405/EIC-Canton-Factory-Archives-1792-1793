/**
 * Types for the review workflow added in Stage 10.
 *
 * Three dimensions are kept apart on purpose and never collapsed into one badge:
 *   1. `source_kind` — what produced the record (provider / seed / rule stub / mock);
 *   2. `claim_status` / `review_status` — what a human decided inside this tool;
 *   3. `synthetic` — whether the record may ever be presented as an archival finding.
 */

export type SourceKind =
  | "provider_reported"
  | "historical_seed"
  | "mock_text_derived"
  | "mock_constant"
  | "unknown";

export type ClaimStatusValue =
  | "asserted"
  | "accepted"
  | "uncertain"
  | "contested"
  | "rejected";

export type ReviewStatusValue = "unreviewed" | "in_review" | "reviewed";

export interface ReviewRelationshipRow {
  id: number;
  source_person_id: number;
  target_person_id: number;
  source_name?: string | null;
  target_name?: string | null;
  relation_type: string;
  relation_label?: string | null;
  claim_status: ClaimStatusValue;
  review_status: ReviewStatusValue;
  evidence_count: number;
  has_date: boolean;
  valid_from?: string | null;
  valid_to?: string | null;
  time_description?: string | null;
  source_kind: SourceKind;
  source_kind_label: string;
  source_kinds: Record<string, number>;
  synthetic: boolean;
  reviewable: boolean;
}

export interface ReviewCounts {
  by_claim_status?: Record<string, number>;
  by_review_status?: Record<string, number>;
  by_relation_type?: Record<string, number>;
  by_source_kind?: Record<string, number>;
}

export interface RelationshipReviewList {
  group_id: number;
  group_name: string;
  total: number;
  skip: number;
  limit: number;
  items: ReviewRelationshipRow[];
  counts: ReviewCounts;
  data_trust?: TrustPayload | null;
}

export interface TrustPayload {
  source_kinds?: Record<string, number>;
  synthetic?: boolean;
  models?: string[];
  labels?: Record<string, string>;
  note?: string;
}

export interface RelationshipReviewResult {
  relationship: ReviewRelationshipRow;
  audit_id: number;
  source_kind: SourceKind;
  source_kind_label: string;
  synthetic: boolean;
  warning?: string | null;
}

export interface MentionCluster {
  normalized_text: string;
  mention_count: number;
  block_count: number;
  resolved_person_names: string[];
  source_kinds: Record<string, number>;
  synthetic: boolean;
  sample_mentions: {
    mention_id: number;
    raw_text: string;
    block_id: number;
    temporal_year?: number | null;
    evidence_text?: string | null;
    source_kind: SourceKind;
  }[];
}

export interface MentionClusterList {
  group_id: number;
  group_name: string;
  total_mentions: number;
  total_clusters: number;
  items: MentionCluster[];
  data_trust?: TrustPayload | null;
}

export interface BatchLinkPreview {
  group_id: number;
  group_name: string;
  normalized_text: string;
  target_person_id?: number | null;
  target_person_name: string;
  create_new_person: boolean;
  mention_count: number;
  mention_ids: number[];
  years: number[];
  source_kinds: Record<string, number>;
  synthetic: boolean;
  warnings: string[];
  confirm_token: string;
}

export interface BatchLinkResult {
  batch_id: string;
  person_id: number;
  person_name: string;
  created_person: boolean;
  mentions_linked: number;
  synthetic: boolean;
  source_kinds: Record<string, number>;
  warnings: string[];
  audit_ids: number[];
}

export interface SnapshotSummary {
  run_id: string;
  name?: string | null;
  group_id?: number | null;
  group_name?: string | null;
  view?: string | null;
  node_count: number;
  edge_count: number;
  capability?: string | null;
  synthetic: boolean;
  created_by?: string | null;
  created_at?: string | null;
}

export interface SnapshotDetail extends SnapshotSummary {
  note?: string | null;
  filters?: Record<string, unknown> | null;
  parameters?: Record<string, unknown> | null;
  algorithm_version?: string | null;
  persons: { id: number; display_name?: string | null; identity_status?: string | null }[];
  relationships: {
    id: number;
    source_person_id: number;
    target_person_id: number;
    relation_type: string;
    claim_status: string;
    review_status?: string | null;
    evidence_count?: number;
    source_kinds?: Record<string, number>;
  }[];
  metrics_summary?: Record<string, unknown> | null;
  excluded?: Record<string, unknown> | null;
  data_trust?: TrustPayload | null;
  extraction_run_ids: number[];
  capabilities?: {
    can_display?: boolean;
    can_rebuild?: boolean;
    can_recompute?: boolean;
    explanation?: string;
  } | null;
}

export interface SnapshotCompare {
  left: SnapshotSummary;
  right: SnapshotSummary;
  parameters_changed: boolean;
  view_changed: boolean;
  structure: {
    persons_added?: number[];
    persons_removed?: number[];
    relationships_added?: number[];
    relationships_removed?: number[];
  };
  review_changes: { id: number; before: Record<string, unknown>; after: Record<string, unknown> }[];
  evidence_changes: { id: number; before: number | null; after: number | null }[];
  provenance_changes: { id: number; before: Record<string, number>; after: Record<string, number> }[];
  provenance_summary: { before?: Record<string, number>; after?: Record<string, number> };
  notes: string[];
}

/** Short label for a provenance kind; unknown keys are shown verbatim. */
export const SOURCE_KIND_SHORT: Record<string, string> = {
  provider_reported: "真实模型",
  historical_seed: "人工种子",
  mock_text_derived: "规则桩",
  mock_constant: "固定 Mock",
  unknown: "来源不明",
};

export function sourceKindShort(kind?: string | null): string {
  if (!kind) return SOURCE_KIND_SHORT.unknown;
  return SOURCE_KIND_SHORT[kind] ?? kind;
}

/** True when a record must keep a synthetic warning wherever it is displayed. */
export function mustWarnSynthetic(kind?: string | null): boolean {
  // `unknown` warns too: an unprovable origin is an open question, not a finding.
  if (!kind) return true;
  return (
    kind === "mock_constant" || kind === "mock_text_derived" || kind === "unknown"
  );
}

export function sourceKindSummary(kinds?: Record<string, number> | null): string {
  if (!kinds) return "";
  return Object.entries(kinds)
    .map(([kind, count]) => `${sourceKindShort(kind)} ${count}`)
    .join(" · ");
}
