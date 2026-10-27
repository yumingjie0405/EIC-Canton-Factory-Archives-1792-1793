"""Document API routes."""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.services.document_progress import processing_summaries
from app.services import DocumentService
from app.schemas import (
    DocumentResponse,
    DocumentListResponse,
    DocumentDetailResponse,
    RunOCRRequest,
    TaskResponse,
)
from app.models import DocumentStatus, TaskType
from app.repositories import TaskRepository, PageRepository
from app.tasks import run_ocr_task
from app.schemas.task import TaskCreate
from app.core import logger, DocumentNotFoundError
from app.config import settings

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload a new document (PDF, image or Word .docx)."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(content) > settings.max_upload_file_bytes:
        raise HTTPException(
            status_code=413,
            detail="文件为 %.1f MB，超过单文件上限 %d MB。"
            % (len(content) / (1024 * 1024), settings.max_upload_file_mb),
        )

    service = DocumentService(db)
    try:
        document = await service.upload_document(file.filename, content)
        return document
    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=DocumentListResponse)
def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: Optional[DocumentStatus] = None,
    archived: bool = False,
    db: Session = Depends(get_db),
):
    """List all documents with pagination."""
    service = DocumentService(db)
    documents, total = service.get_documents(page, page_size, status, archived)
    summaries = processing_summaries(db, [d.id for d in documents])
    return DocumentListResponse(
        items=[
            DocumentResponse.model_validate(d).model_copy(
                update={"processing_summary": summaries[d.id]}
            )
            for d in documents
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document(
    document_id: int,
    db: Session = Depends(get_db),
):
    """Get document details."""
    service = DocumentService(db)
    document = service.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return DocumentDetailResponse.model_validate(document).model_copy(
        update={
            "processing_summary": processing_summaries(db, [document_id])[document_id]
        }
    )


@router.delete("/{document_id}")
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
):
    """Delete a document."""
    service = DocumentService(db)
    if not service.delete_document(document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return {"message": "文档已移入回收站，已有引用和文件保留，可随时恢复"}


@router.post("/{document_id}/restore")
def restore_document(document_id: int, db: Session = Depends(get_db)):
    if not DocumentService(db).restore_document(document_id):
        raise HTTPException(404, "文档不存在")
    return {"message": "文档已恢复"}


@router.post("/{document_id}/run-ocr", response_model=TaskResponse)
def run_ocr(
    document_id: int,
    request: RunOCRRequest = RunOCRRequest(),
    db: Session = Depends(get_db),
):
    """Start OCR processing for a document."""
    service = DocumentService(db)
    document = service.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Create task record
    task_repo = TaskRepository(db)
    task_data = TaskCreate(document_id=document_id, task_type=TaskType.OCR)
    task = task_repo.create(task_data)

    # Start Celery task
    celery_task = run_ocr_task.delay(
        task_id=task.id,
        document_id=document_id,
        visualize=request.visualize,
        restructure_pages=request.restructure_pages,
        merge_tables=request.merge_tables,
        relevel_titles=request.relevel_titles,
    )

    # Update task with Celery ID
    task_repo.set_celery_id(task.id, celery_task.id)

    return task_repo.get_by_id(task.id)


@router.get("/{document_id}/pages")
def get_document_pages(
    document_id: int,
    db: Session = Depends(get_db),
):
    """Get all pages for a document."""
    service = DocumentService(db)
    document = service.get_document(document_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    page_repo = PageRepository(db)
    pages = page_repo.get_by_document(document_id)
    return {"items": pages, "total": len(pages)}
