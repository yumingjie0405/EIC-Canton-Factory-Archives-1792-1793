"""Pydantic schemas for Stage 7: AI Agent dynamic research workflow."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.schemas.rag import (
    AnswerGeneration,
    HistoryLink,
    RagSourceEvidence,
    RagEntityReference,
    RagRelationshipReference,
)


class ToolDefinition(BaseModel):
    """Specification of an available agent tool."""
    name: str = Field(..., description="Unique tool identifier")
    description: str = Field(..., description="Description of the tool capability")
    parameters: Dict[str, Any] = Field(..., description="JSON Schema of required and optional parameters")


class ToolCallRecord(BaseModel):
    """Audit log entry for a single executed tool invocation."""
    call_id: str
    tool_name: str
    arguments: Dict[str, Any]
    status: str = "success"  # "success" | "error" | "intercepted"
    output_summary: str
    latency_ms: float
    evidence_count: int = 0
    error_message: Optional[str] = None


class AgentStepRecord(BaseModel):
    """Single step in the Agent's reasoning and execution trajectory."""
    step_number: int
    sub_goal: str
    thought: str
    tool_calls: List[ToolCallRecord] = Field(default_factory=list)
    step_status: str = "completed"  # "pending" | "running" | "completed" | "failed"


class AnswerCategorization(BaseModel):
    """Categorized breakdown of research conclusions to prevent hallucination."""
    archival_facts: List[str] = Field(
        default_factory=list,
        description="Statements directly verified by 1792-1793 Canton Factory archival text blocks with citations"
    )
    kg_relationships: List[str] = Field(
        default_factory=list,
        description="Structured relationship and entity facts retrieved from verified knowledge graph"
    )
    external_scholarship: List[str] = Field(
        default_factory=list,
        description="Authoritative external historiography (e.g. Liang Jiabin's Study of Canton Thirteen Hongs)"
    )
    inferences_and_synthesis: List[str] = Field(
        default_factory=list,
        description="Reasonable historical inferences explicitly labeled as deduction rather than raw archive fact"
    )
    unresolved_aspects: List[str] = Field(
        default_factory=list,
        description="Questions or areas where evidence was insufficient or unrecorded in the archives"
    )


class AgentResearchRequest(BaseModel):
    """Request payload to initiate autonomous historical research."""
    question: str = Field(..., min_length=2, max_length=500, description="Historical research inquiry")
    max_steps: int = Field(default=4, ge=1, le=8, description="Maximum planning and reflection steps")
    max_tool_calls: int = Field(default=6, ge=1, le=12, description="Maximum total tool invocations budget")
    time_filter: Optional[int] = Field(default=None, description="Optional target year (e.g. 1792 or 1793)")
    temperature: float = Field(default=0.0, ge=0.0, le=1.0, description="Model generation temperature")
    include_trajectory: bool = Field(default=True, description="Whether to include full reasoning trajectory")
    group_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Document group to research. Resolved server-side; unknown groups are rejected",
    )


class AgentBudgetInfo(BaseModel):
    """Resource budget and execution metrics monitoring."""
    steps_executed: int = Field(default=0, description="Number of reasoning steps executed")
    max_steps: int = Field(default=4, description="Maximum planning steps configured")
    tool_calls_executed: int = Field(default=0, description="Total tool invocations executed")
    max_tool_calls: int = Field(default=6, description="Maximum tool call budget configured")
    elapsed_ms: float = Field(default=0.0, description="Total elapsed workflow latency in ms")
    budget_exhausted: bool = Field(default=False, description="Whether execution was stopped by budget limits")

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


class AgentResearchResponse(BaseModel):
    """Comprehensive response from autonomous research agent."""
    task_id: str
    question: str
    final_answer: str
    categorization: AnswerCategorization
    trajectory: List[AgentStepRecord] = Field(default_factory=list)
    sources: List[RagSourceEvidence] = Field(default_factory=list)
    entities: List[RagEntityReference] = Field(default_factory=list)
    relationships: List[RagRelationshipReference] = Field(default_factory=list)
    evidence_sufficient: bool = True
    budget_used: AgentBudgetInfo = Field(default_factory=AgentBudgetInfo)
    warnings: List[str] = Field(default_factory=list)
    group_id: Optional[int] = Field(None, description="Research group this run was scoped to")
    group_name: Optional[str] = Field(None, description="Group name captured at run time")
    generation: AnswerGeneration = Field(
        default_factory=AnswerGeneration,
        description="Where the final_answer text came from and whether citation checks passed",
    )
    history: HistoryLink = Field(
        default_factory=HistoryLink,
        description="Whether this run was persisted and under which record id",
    )


class AgentPlanResponse(BaseModel):
    """Decomposed research plan preview."""
    question: str
    proposed_steps: List[str]
    target_entities: List[str] = Field(default_factory=list)
    time_filter: Optional[int] = None
    estimated_tool_calls: int
