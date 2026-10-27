"""FastAPI router for Stage 7: Autonomous Historical Research Agent."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core import logger
from app.database import get_db
from app.schemas.agent import (
    AgentPlanResponse,
    AgentResearchRequest,
    AgentResearchResponse,
    ToolDefinition,
)
from app.services.agent import agent_service
from app.services.rag.answer_integrity import scrub_secrets
from app.services.research_history_service import MODE_AGENT, research_history_service
from app.services.group_scope import GroupNotFound, optional_group_scope

router = APIRouter(prefix="/agent", tags=["AI Historical Research Agent"])


@router.post(
    "/research",
    response_model=AgentResearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute multi-step autonomous historical research workflow",
)
async def run_agent_research(
    request: AgentResearchRequest,
    db: Session = Depends(get_db),
):
    """
    Execute autonomous historical investigation:
    1. Decompose research question into finite verifiable steps;
    2. Invoke whitelisted read-only tools with input validation and budget caps;
    3. Evaluate evidence sufficiency dynamically;
    4. Enforce anti-hallucination gates (zero citations for fictitious queries);
    5. Generate four-level categorized synthesis grounded in real archival citations.
    """
    try:
        scope = optional_group_scope(db, request.group_id)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    group_ids = set(scope.document_ids) if scope is not None else None
    try:
        response = await agent_service.run_research(
            db=db, request=request, group_document_ids=group_ids
        )
    except Exception as exc:
        logger.error("Error executing Agent research workflow: %s", exc, exc_info=True)
        research_history_service.save_failure(
            db,
            MODE_AGENT,
            request.question,
            exc,
            group_id=scope.group_id if scope else None,
            group_name=scope.group_name if scope else None,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent research workflow failed: %s" % scrub_secrets(str(exc)),
        )

    if scope is not None:
        response.group_id = scope.group_id
        response.group_name = scope.group_name
    response.history = research_history_service.save_response(
        db,
        MODE_AGENT,
        response,
        question=request.question,
        group_id=scope.group_id if scope else None,
        group_name=scope.group_name if scope else None,
    )
    return response


@router.post(
    "/plan",
    response_model=AgentPlanResponse,
    status_code=status.HTTP_200_OK,
    summary="Preview decomposed research plan without executing full tool cycle",
)
async def preview_research_plan(
    question: str = Query(..., min_length=2, max_length=500, description="Research question to decompose"),
    time_filter: Optional[int] = Query(None, description="Optional target year constraint"),
    db: Session = Depends(get_db),
):
    """Generate and inspect preliminary sub-goals for a historical inquiry."""
    try:
        return await agent_service.preview_plan(db=db, question=question, time_filter=time_filter)
    except Exception as exc:
        logger.error("Error decomposing research plan: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Plan decomposition failed: {scrub_secrets(str(exc))}",
        )


@router.get(
    "/tools",
    response_model=List[ToolDefinition],
    status_code=status.HTTP_200_OK,
    summary="List authorized research tools and parameters in the security whitelist",
)
def list_agent_tools():
    """Return catalog of public whitelisted read-only tools."""
    return agent_service.get_available_tools()
