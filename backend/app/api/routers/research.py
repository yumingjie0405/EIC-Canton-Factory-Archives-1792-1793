"""Read-only API for saved research records (RAG answers and Agent research runs)."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import logger
from app.database import get_db
from app.schemas.research import (
    ResearchHistoryListResponse,
    ResearchRecordDetail,
    build_research_detail,
    build_research_summary,
)
from app.services.research_history_service import research_history_service
from app.services.rag.answer_integrity import scrub_secrets

router = APIRouter(prefix="/research", tags=["Research History"])


@router.get(
    "/history",
    response_model=ResearchHistoryListResponse,
    status_code=status.HTTP_200_OK,
    summary="List saved research records, newest first",
)
def list_research_history(
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(20, ge=1, le=100, description="Records per page"),
    mode: Optional[str] = Query(None, pattern="^(rag|agent)$", description="Filter by research pipeline"),
    group_id: Optional[int] = Query(None, ge=1, description="Filter by research group"),
    db: Session = Depends(get_db),
):
    """List saved research runs (question, status and generation metadata only)."""
    # Direct calls (tests, internal services) receive the FastAPI ``Query`` default
    # object; only a real integer narrows the list.
    resolved_group_id = group_id if isinstance(group_id, int) else None
    try:
        records, total = research_history_service.list_records(
            db, page=page, page_size=page_size, mode=mode, group_id=resolved_group_id
        )
    except SQLAlchemyError as exc:
        logger.error("Research history list failed: %s", scrub_secrets(str(exc)))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="研究历史暂时不可用：数据库读取失败（research_sessions 表可能尚未建表）。",
        )

    return ResearchHistoryListResponse(
        items=[build_research_summary(record) for record in records],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/history/{record_id}",
    response_model=ResearchRecordDetail,
    status_code=status.HTTP_200_OK,
    summary="Get one saved research record with its evidence snapshot",
)
def get_research_record(
    record_id: int = Path(..., ge=1, description="Saved research record id"),
    db: Session = Depends(get_db),
):
    """Return the stored answer and evidence snapshot.

    Nothing is re-retrieved and the model is not called again, so a reopened
    answer cannot drift when the index, the corpus or the model changes.
    """
    try:
        record = research_history_service.get_record(db, record_id)
    except SQLAlchemyError as exc:
        logger.error("Research history detail failed: %s", scrub_secrets(str(exc)))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="研究历史暂时不可用：数据库读取失败（research_sessions 表可能尚未建表）。",
        )

    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Research record %d not found" % record_id,
        )
    return build_research_detail(record)
