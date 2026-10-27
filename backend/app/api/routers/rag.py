"""FastAPI router for Stage 6: Graph RAG intelligent Q&A and retrieval."""
from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.core import logger
from app.database import get_db
from app.schemas.rag import (
    QueryIntentAnalysis,
    RagEntityContextResponse,
    RagQueryRequest,
    RagQueryResponse,
)
from app.services.rag import query_analyzer, rag_service
from app.services.rag.answer_integrity import scrub_secrets
from app.services.research_history_service import MODE_RAG, research_history_service
from app.services.group_scope import GroupNotFound, optional_group_scope

router = APIRouter(prefix="/rag", tags=["Graph RAG Question Answering"])


@router.post(
    "/query",
    response_model=RagQueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Query Graph RAG system with natural language question",
)
async def query_graph_rag(
    request: RagQueryRequest,
    db: Session = Depends(get_db),
):
    """
    Execute Graph RAG Q&A:
    1. Understand user intent and extract entities;
    2. Retrieve structured facts from knowledge graph;
    3. Retrieve raw archival text evidence from blocks;
    4. Fuse context and synthesize cited research answer with hallucination prevention.
    """
    try:
        scope = optional_group_scope(db, request.group_id)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    group_ids = set(scope.document_ids) if scope is not None else None
    try:
        response = await rag_service.answer_question(
            db=db, request=request, group_document_ids=group_ids
        )
    except Exception as exc:
        logger.error("Error executing Graph RAG query: %s", exc)
        research_history_service.save_failure(
            db,
            MODE_RAG,
            request.question,
            exc,
            group_id=scope.group_id if scope else None,
            group_name=scope.group_name if scope else None,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Graph RAG query failed: %s" % scrub_secrets(str(exc)),
        )

    if scope is not None:
        response.group_id = scope.group_id
        response.group_name = scope.group_name
    response.history = research_history_service.save_response(
        db,
        MODE_RAG,
        response,
        question=request.question,
        group_id=scope.group_id if scope else None,
        group_name=scope.group_name if scope else None,
    )
    return response


@router.get(
    "/context/{entity_id}",
    response_model=RagEntityContextResponse,
    status_code=status.HTTP_200_OK,
    summary="Get subgraph context and grounded archival citations for an entity",
)
def get_entity_rag_context(
    entity_id: int = Path(..., description="Canonical Person entity ID"),
    db: Session = Depends(get_db),
):
    """Retrieve 1-degree ego network, relationships, and textual evidence for an entity."""
    try:
        return rag_service.get_entity_context(db=db, entity_id=entity_id)
    except Exception as exc:
        logger.error("Error retrieving entity context for RAG: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@router.post(
    "/analyze",
    response_model=QueryIntentAnalysis,
    summary="Analyze query intent and recognize target entities without executing full synthesis",
)
def analyze_query_intent(
    query_text: str,
    db: Session = Depends(get_db),
):
    """Analyze query intent, extract historical entities, and identify time scope."""
    return query_analyzer.analyze_query(query=query_text, db=db)
