"""Pydantic schemas for Social Network Analysis (SNA) query, computation, and snapshots."""
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.kg.enums import RelationType


class SNAGraphFilterParams(BaseModel):
    """Parameters for filtering historical graph entities and relationships."""

    relation_types: Optional[List[RelationType]] = Field(
        None, description="Filter specific historical relationship types (e.g. commercial, guarantee)"
    )
    period_start: Optional[date] = Field(
        None, description="Earliest date filter (inclusive)"
    )
    period_end: Optional[date] = Field(
        None, description="Latest date filter (inclusive)"
    )
    include_unknown_dates: bool = Field(
        True, description="Whether to include relationships where valid_from/valid_to are unspecified"
    )
    min_confidence: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Minimum confidence threshold (0.0 to 1.0)"
    )
    include_provisional: bool = Field(
        False, description="Whether to include provisional persons (default: only accepted persons)"
    )
    include_isolates: bool = Field(
        True, description="Whether to retain persons with zero degrees in the network"
    )
    directed: bool = Field(
        True, description="Whether edges preserve direction (source -> target)"
    )
    group_id: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Restrict the network to one document group. Membership is derived from that "
            "group's own evidence (blocks -> mentions / relationship evidences); there is no "
            "fallback to the global graph when the scope is empty or unknown."
        ),
    )
    view: Optional[str] = Field(
        None,
        pattern="^(official|candidates|all)$",
        description=(
            "all: every extracted relationship except rejected (default for a group, so a "
            "fresh extraction is visible together with its sources); official: accepted "
            "only; candidates: not-yet-accepted only. Unset = legacy global behaviour."
        ),
    )

    @model_validator(mode="after")
    def validate_date_range(self) -> "SNAGraphFilterParams":
        if self.period_start is not None and self.period_end is not None:
            if self.period_start > self.period_end:
                raise ValueError(
                    f"period_start ({self.period_start}) cannot be after period_end ({self.period_end})"
                )
        return self

    def resolved_view(self) -> Optional[str]:
        """A group shows everything extracted (with sources); global keeps legacy rules."""
        if self.view:
            return self.view
        return "all" if self.group_id is not None else None


class SNANetworkSummaryResponse(BaseModel):
    """Network-level macro topological summary."""
    node_count: int = Field(..., description="Number of person nodes included in the network")
    edge_count: int = Field(..., description="Count of raw filtered relationships in database")
    aggregated_edge_count: int = Field(..., description="Count of aggregated directed/undirected pairs in topology")
    density: float = Field(..., description="Network density (actual edges / maximum possible edges)")
    average_degree: float = Field(..., description="Mean degree across all nodes")
    average_in_degree: Optional[float] = Field(None, description="Mean in-degree (for directed graphs)")
    average_out_degree: Optional[float] = Field(None, description="Mean out-degree (for directed graphs)")
    connected_components_count: int = Field(..., description="Number of weakly connected components")
    component_sizes: List[int] = Field(default_factory=list, description="Sizes of components ordered descending")
    average_clustering_coefficient: float = Field(..., description="Mean local clustering coefficient")
    isolated_nodes_count: int = Field(..., description="Number of nodes with degree = 0")
    filter_parameters: Dict[str, Any] = Field(..., description="Filter conditions used for this analysis run")
    algorithm_version: str = Field("NativeSNAEngine-v1.0", description="Algorithm version identifier")
    group_id: Optional[int] = Field(None, description="Document group the network was scoped to (None = global)")
    group_name: Optional[str] = Field(None, description="Name of the scoped document group")
    view: Optional[str] = Field(None, description="official | candidates | None (global legacy view)")
    scope_document_count: Optional[int] = Field(None, description="Documents in the scoped group")
    scope_block_count: Optional[int] = Field(None, description="Text blocks forming the group evidence domain")
    data_limits_note: str = Field(
        "Reflects surviving East India Company Canton Factory records (1792-1793). Not a causal measure of total historical influence.",
        description="Epistemological archival boundary disclaimer",
    )
    edge_source_composition: Dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Number of edges per combined provenance kind. A network whose edges are "
            "mock-derived measures the extractor, not the historical record."
        ),
    )
    verified_edge_count: int = Field(
        0, description="Edges supported only by provider/seed evidence"
    )
    synthetic_edge_count: int = Field(0, description="Edges supported by mock/stub evidence")
    unknown_edge_count: int = Field(
        0, description="Edges whose evidence origin cannot be reconstructed"
    )
    metrics_basis: str = Field(
        "",
        description=(
            "How the centrality weights were built, stated explicitly so the numbers are "
            "not read as a claim about the archive itself"
        ),
    )


class SNAPersonMetricsResponse(BaseModel):
    """Node-level SNA centrality and topology metrics for a specific historical person."""
    person_id: int
    display_name: str
    canonical_name_en: Optional[str] = None
    canonical_name_zh: Optional[str] = None
    primary_role: Optional[str] = None
    organization: Optional[str] = None
    in_network: bool = Field(..., description="Whether person meets filters and is included in network")
    degree: int = 0
    in_degree: int = 0
    out_degree: int = 0
    degree_centrality: float = 0.0
    betweenness_centrality: float = 0.0
    closeness_centrality: float = 0.0
    pagerank: float = 0.0
    clustering_coefficient: float = 0.0
    community_id: Optional[str] = None
    rank_degree: Optional[int] = None
    rank_betweenness: Optional[int] = None
    rank_closeness: Optional[int] = None
    rank_pagerank: Optional[int] = None
    note: Optional[str] = None


class SNAPersonNeighborEvidence(BaseModel):
    """Archival evidence backing an edge connecting two persons."""
    id: int
    relationship_id: int
    block_id: int
    quote_text: Optional[str] = None
    evidence_role: str


class SNAPersonNeighborItem(BaseModel):
    """Direct topological neighbor of a person and connecting archival relationships."""
    person_id: int
    display_name: str
    canonical_name_en: Optional[str] = None
    canonical_name_zh: Optional[str] = None
    primary_role: Optional[str] = None
    organization: Optional[str] = None
    direction: str = Field(..., description="'out' (target), 'in' (source), or 'mutual'")
    relation_count: int
    relation_types: List[str]
    evidence_count: int
    evidence_block_ids: List[int]
    evidences: List[SNAPersonNeighborEvidence] = Field(default_factory=list)


class SNAPersonNeighborsResponse(BaseModel):
    """Ego-network response listing direct neighbors and grounded historical evidence."""
    person_id: int
    display_name: str
    neighbor_count: int
    neighbors: List[SNAPersonNeighborItem]


class SNASnapshotCreateRequest(BaseModel):
    """Request payload to compute and persist an SNA snapshot."""
    run_id: Optional[str] = Field(None, description="Optional custom run ID; defaults to auto-generated UUID")
    graph_scope: str = Field("canton_1792_1793", description="Archival or topical scope identifier")
    filters: Optional[SNAGraphFilterParams] = Field(default_factory=SNAGraphFilterParams)


class SNASnapshotResponse(BaseModel):
    """Response confirming SNA metric snapshot persistence."""
    run_id: str
    snapshot_at: datetime
    graph_scope: str
    node_count: int
    metrics_persisted_count: int
    summary: SNANetworkSummaryResponse
