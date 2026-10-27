"""Block API routes."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.repositories import BlockRepository, DocumentRepository, TaskRepository, PageRepository
from app.schemas import BlockResponse, BlockUpdate, TaskResponse, RunPolishRequest
from app.schemas.block_context import BlockContextResponse
from app.schemas.task import TaskCreate
from app.models import TaskType
from app.services.block_context_service import (
    NEIGHBOUR_SCOPE,
    NOTES,
    build_block_context,
    to_context_block,
)
from app.services.rag.answer_integrity import scrub_secrets
from app.tasks import polish_block_task, polish_page_task, polish_document_task
from app.tasks.gpt_tasks_batch import polish_document_batch_task
from app.core import logger

router = APIRouter(prefix="/blocks", tags=["blocks"])


def _validate_page_modes(request: RunPolishRequest) -> None:
    """Require strict OCR correction for page and document tasks."""
    if request.modes != ["ocr_correction"]:
        raise HTTPException(
            status_code=422,
            detail="Page and document polish support only ocr_correction mode",
        )


@router.get("/{block_id}/context", response_model=BlockContextResponse)
def get_block_context(
    block_id: int = Path(..., ge=1, description="Cited block id"),
    document_id: int = Query(..., ge=1, description="Document the citation claims to come from"),
    window: int = Query(1, ge=0, le=3, description="Neighbour blocks before/after on the same page"),
    db: Session = Depends(get_db),
):
    """Return the cited archival block and its same-page neighbours (read-only).

    The block/document pair is validated server-side so a mismatched id
    combination can never return text from another document. Only stored OCR
    transcriptions are returned — the model-derived fields are excluded.
    """
    try:
        block = BlockRepository(db).get_by_id(block_id)
        if not block:
            raise HTTPException(status_code=404, detail="Block not found")

        page = PageRepository(db).get_by_id(block.page_id)
        if page is None:
            raise HTTPException(status_code=404, detail="Page for this block no longer exists")
        if page.document_id != document_id:
            raise HTTPException(
                status_code=404,
                detail="Block %d does not belong to document %d" % (block_id, document_id),
            )

        document = DocumentRepository(db).get_by_id(document_id)
        if document is None:
            raise HTTPException(status_code=404, detail="Document not found")

        siblings = BlockRepository(db).get_by_page(block.page_id)
        neighbours = build_block_context(block, siblings, window=window)
    except HTTPException:
        raise
    except SQLAlchemyError as exc:
        logger.error("Block context lookup failed: %s", scrub_secrets(str(exc)))
        raise HTTPException(
            status_code=503,
            detail="档案原文暂时无法读取：数据库读取失败。",
        )

    return BlockContextResponse(
        document_id=document_id,
        document_name=document.original_filename or document.filename or "未命名档案",
        page_id=page.id,
        page_number=page.page_number,
        block=to_context_block(block),
        previous=neighbours["previous"],
        next=neighbours["next"],
        neighbour_scope=NEIGHBOUR_SCOPE,
        notes=list(NOTES),
    )


@router.get("/{block_id}", response_model=BlockResponse)
def get_block(
    block_id: int,
    db: Session = Depends(get_db),
):
    """Get block details."""
    block_repo = BlockRepository(db)
    block = block_repo.get_by_id(block_id)
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    return block


@router.patch("/{block_id}", response_model=BlockResponse)
def update_block(
    block_id: int,
    data: BlockUpdate,
    db: Session = Depends(get_db),
):
    """Update block fields."""
    block_repo = BlockRepository(db)
    block = block_repo.update(block_id, data)
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    return block


@router.post("/{block_id}/run-polish", response_model=TaskResponse)
def run_polish_block(
    block_id: int,
    request: RunPolishRequest = RunPolishRequest(),
    db: Session = Depends(get_db),
):
    """Run GPT polish on a single block."""
    block_repo = BlockRepository(db)
    block = block_repo.get_by_id(block_id)
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")

    if not block.ocr_text_raw:
        raise HTTPException(status_code=400, detail="Block has no OCR text")

    # Create task
    task_repo = TaskRepository(db)
    task_data = TaskCreate(
        block_id=block_id,
        page_id=block.page_id,
        task_type=TaskType.OCR_CORRECTION,
    )
    task = task_repo.create(task_data)

    # Start Celery task
    celery_task = polish_block_task.delay(
        task_id=task.id,
        block_id=block_id,
        modes=request.modes,
    )
    task_repo.set_celery_id(task.id, celery_task.id)

    return task_repo.get_by_id(task.id)


# Page-level polish endpoint
@router.post("/pages/{page_id}/run-polish", response_model=TaskResponse)
def run_polish_page(
    page_id: int,
    request: RunPolishRequest = RunPolishRequest(),
    db: Session = Depends(get_db),
):
    """Run GPT polish on all blocks in a page."""
    _validate_page_modes(request)
    page_repo = PageRepository(db)
    page = page_repo.get_by_id(page_id)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    # Create task
    task_repo = TaskRepository(db)
    task_data = TaskCreate(
        page_id=page_id,
        document_id=page.document_id,
        task_type=TaskType.OCR_CORRECTION,
    )
    task = task_repo.create(task_data)

    # Start Celery task
    celery_task = polish_page_task.delay(
        task_id=task.id,
        page_id=page_id,
        modes=request.modes,
    )
    task_repo.set_celery_id(task.id, celery_task.id)

    return task_repo.get_by_id(task.id)


# Document-level polish endpoint
@router.post("/documents/{document_id}/run-polish", response_model=TaskResponse)
def run_polish_document(
    document_id: int,
    request: RunPolishRequest = RunPolishRequest(),
    db: Session = Depends(get_db),
):
    """Run GPT polish on all blocks in a document."""
    from app.services import DocumentService

    _validate_page_modes(request)
    service = DocumentService(db)
    document = service.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Create task
    task_repo = TaskRepository(db)
    task_data = TaskCreate(
        document_id=document_id,
        task_type=TaskType.OCR_CORRECTION,
    )
    task = task_repo.create(task_data)

    # Start Celery task
    celery_task = polish_document_task.delay(
        task_id=task.id,
        document_id=document_id,
        modes=request.modes,
    )
    task_repo.set_celery_id(task.id, celery_task.id)

    return task_repo.get_by_id(task.id)
