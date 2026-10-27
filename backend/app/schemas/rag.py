"""Pydantic schemas for Stage 6: Graph RAG intelligent retrieval and question answering."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RagQueryRequest(BaseModel):
    """User query request payload for Graph RAG."""
    question: str = Field(..., min_length=1, max_length=1000, description="Natural language historical research question")
    top_k: int = Field(5, ge=1, le=20, description="Number of text evidence blocks to retrieve")
    include_graph: bool = Field(True, description="Whether to perform knowledge graph expansion")
    time_filter: Optional[int] = Field(None, description="Optional temporal year filter (e.g. 1792, 1793)")
    group_id: Optional[int] = Field(
        None,
        ge=1,
        description="Document group to search. Resolved server-side; unknown groups are rejected",
    )


class QueryIntentAnalysis(BaseModel):
    """Extracted query intent, entities, and temporal filters."""
    raw_query: str
    query_type: str = Field("general", description="'factual', 'relational', 'historical_analysis', or 'unknown'")
    entities: List[str] = Field(default_factory=list, description="Extracted entity mentions or names")
    time_filter: Optional[int] = None
    spatial_filter: Optional[str] = None
    search_keywords: List[str] = Field(default_factory=list)


class RagEntityReference(BaseModel):
    """Historical entity referenced in knowledge graph and answer."""
    id: int
    display_name: str
    primary_role: Optional[str] = None
    canonical_name_zh: Optional[str] = None
    canonical_name_en: Optional[str] = None
    organization: Optional[str] = None
    identity_status: Optional[str] = None
    external_authorities_count: int = 0
    external_records: List[str] = Field(default_factory=list)


class RagRelationshipReference(BaseModel):
    """Historical relationship fact retrieved from knowledge graph."""
    source_person_name: str
    target_person_name: str
    relation_type: str
    time_description: Optional[str] = None
    confidence: Optional[float] = None
    claim_status: Optional[str] = None
    review_status: Optional[str] = None
    evidence_quote: Optional[str] = None


class RagSourceEvidence(BaseModel):
    """Archival text evidence grounding a citation in the answer."""
    source_id: str = Field(..., description="Citation marker e.g. '证据 #1'")
    document_id: Optional[int] = None
    document_name: str = Field("未知档案文献", description="Name of the historical archival document")
    page_number: Optional[int] = None
    block_id: int
    quote_text: str = Field(..., description="Original historical textual excerpt")
    relevance_score: float = Field(0.0, description="Relevance score in retrieval (0.0 to 1.0)")
    matched_entities: List[str] = Field(default_factory=list)
    evidence_type: str = Field("primary_archival", description="'primary_archival', 'relationship_metadata', or 'external_scholarship'")
    temporal_coverage: Optional[str] = Field(None, description="Document temporal coverage e.g. '1792–1793年 [经过审定]'")
    temporal_provenance: Optional[str] = Field(None, description="'human_verified', 'filename_inferred', 'content_inferred', or 'unknown'")


class CitationIntegrity(BaseModel):
    """Result of verifying inline ``[证据 #N]`` markers against retrieved evidence."""
    valid: bool = Field(True, description="False if the answer cites evidence numbers that do not exist")
    cited_ids: List[int] = Field(default_factory=list, description="Evidence numbers actually cited")
    invalid_ids: List[int] = Field(default_factory=list, description="Cited numbers with no matching source")
    available_count: int = Field(0, description="Number of retrievable evidence items offered to the model")


class HistoryLink(BaseModel):
    """Whether the finished research run was persisted, and under which record id."""
    saved: bool = Field(False, description="True only when the record was committed to the database")
    record_id: Optional[int] = Field(None, description="Record id for GET /api/research/history/{id}")
    mode: Optional[str] = Field(None, description="'rag' or 'agent'")
    error: Optional[str] = Field(None, description="Non-secret reason when saving failed")


class AnswerGeneration(BaseModel):
    """Provenance of the answer text itself (model vs offline synthesis).

    A degraded answer must never be presented to the user as a normal model
    answer: ``mode`` and ``failure_reason`` make an unavailable model service
    (for example an expired API key returning HTTP 401) visible instead of
    silently falling back to a synthesised text.
    """
    mode: str = Field(
        "model",
        description="'model' (LLM synthesis), 'deterministic_fallback' (offline), or 'refused_no_evidence'",
    )
    degraded: bool = Field(False, description="True if the text is not a normal model synthesis")
    model_name: Optional[str] = Field(None, description="Model identifier actually requested")
    failure_reason: Optional[str] = Field(None, description="Stable cause code, e.g. 'authentication_failed'")
    failure_message: Optional[str] = Field(None, description="Human readable cause without secrets")
    failure_detail: Optional[str] = Field(None, description="Scrubbed diagnostic detail")
    citation_check: Optional[CitationIntegrity] = None


class RagQueryResponse(BaseModel):
    """Complete research-grade answer with structured sources and entities."""
    question: str
    answer: str = Field(..., description="Generated historical research response with inline citations")
    evidence_sufficient: bool = Field(True, description="False if knowledge base lacks sufficient evidence")
    intent: Optional[QueryIntentAnalysis] = None
    entities: List[RagEntityReference] = Field(default_factory=list)
    relationships: List[RagRelationshipReference] = Field(default_factory=list)
    sources: List[RagSourceEvidence] = Field(default_factory=list)
    execution_time_ms: float = 0.0
    group_id: Optional[int] = Field(None, description="Research group this run was scoped to")
    group_name: Optional[str] = Field(None, description="Group name captured at run time")
    generation: AnswerGeneration = Field(
        default_factory=AnswerGeneration,
        description="Where the answer text came from and whether citation checks passed",
    )
    history: HistoryLink = Field(
        default_factory=HistoryLink,
        description="Whether this run was persisted and under which record id",
    )


class RagEntityContextResponse(BaseModel):
    """Contextual subgraph and archival evidence for a specific entity."""
    entity: RagEntityReference
    neighbors: List[RagEntityReference] = Field(default_factory=list)
    relationships: List[RagRelationshipReference] = Field(default_factory=list)
    evidences: List[RagSourceEvidence] = Field(default_factory=list)
