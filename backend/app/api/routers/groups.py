"""Document-group API: group management, membership and batch import.

Scope semantics
---------------
Every research entry point resolves the group **on the server** (see
``app.services.group_scope``); this router only exposes management endpoints.
An unknown group is always ``404`` — there is no fallback to a global search and
no group is ever inferred from client-supplied document ids.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, UploadFile, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core import logger
from app.schemas.research_group import (
    AutoResearchStatusResponse,
    GroupCreate,
    GroupDetailResponse,
    GroupDocumentIdsRequest,
    GroupListResponse,
    GroupMemberDocument,
    GroupMembershipChangeResponse,
    GroupResponse,
    GroupUpdate,
    ImportBatchResponse,
    ImportBatchStatusResponse,
)
from app.services.group_scope import GroupNotFound
from app.services.kg import auto_extraction
from app.services.rag.answer_integrity import scrub_secrets
from app.services.research_group_service import (
    GroupConflict,
    GroupDocumentNotFound,
    ResearchGroupService,
)

from app.config import settings

router = APIRouter(prefix="/groups", tags=["Research Groups"])

#: Formats the existing upload/OCR pipeline actually accepts, plus Word (.docx),
#: whose text is read directly instead of being OCRed.
ACCEPTED_SUFFIXES = (
    ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".docx"
)


def _group_payload(group, counts) -> GroupResponse:
    document_count, ready_count, failed_count = counts
    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        auto_extract=bool(getattr(group, "auto_extract", True)),
        document_count=document_count,
        ready_count=ready_count,
        failed_count=failed_count,
        created_at=group.created_at,
        updated_at=group.updated_at,
    )


@router.get("", response_model=GroupListResponse)
def list_groups(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    q: Optional[str] = Query(None, description="Filter by name"),
    db: Session = Depends(get_db),
):
    """List document groups with membership counts."""
    service = ResearchGroupService(db)
    try:
        rows, total = service.list_groups(page=page, page_size=page_size, query=q)
    except SQLAlchemyError as exc:
        logger.error("Group list failed: %s", scrub_secrets(str(exc)))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="文档组功能暂时不可用：数据库读取失败（研究组表可能尚未建表）。",
        )
    return GroupListResponse(
        items=[_group_payload(group, counts) for group, *counts in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(payload: GroupCreate, db: Session = Depends(get_db)):
    """Create a document group."""
    service = ResearchGroupService(db)
    try:
        group = service.create_group(payload)
    except GroupConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return _group_payload(group, service._counts(group.id))


@router.get("/{group_id}", response_model=GroupDetailResponse)
def get_group(
    group_id: int = Path(..., ge=1),
    refresh: bool = Query(True, description="Recompute member statuses from the documents"),
    db: Session = Depends(get_db),
):
    """Group detail with every member document and its real processing status."""
    service = ResearchGroupService(db)
    try:
        group = service.get_group(group_id)
        members = service.list_members(group_id, refresh=refresh)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SQLAlchemyError as exc:
        logger.error("Group detail failed: %s", scrub_secrets(str(exc)))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="文档组功能暂时不可用：数据库读取失败。",
        )
    warning = None
    if not members:
        warning = "该文档组内没有文档：基于此组的检索不会回退到全库，结果将是空证据与拒答。"
    return GroupDetailResponse(
        group=_group_payload(group, service._counts(group.id)),
        documents=[GroupMemberDocument(**member) for member in members],
        warning=warning,
        auto_research=AutoResearchStatusResponse(**auto_extraction.auto_research_status(group).as_dict()),
    )


@router.patch("/{group_id}", response_model=GroupResponse)
def update_group(
    payload: GroupUpdate,
    group_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    """Rename or re-describe a group."""
    service = ResearchGroupService(db)
    try:
        group = service.update_group(group_id, payload)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except GroupConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return _group_payload(group, service._counts(group.id))


@router.delete("/{group_id}")
def delete_group(group_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    """Delete a group. Documents, OCR results and other groups are untouched."""
    service = ResearchGroupService(db)
    try:
        service.delete_group(group_id)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"message": "Group deleted", "group_id": group_id}


@router.post("/{group_id}/documents", response_model=GroupMembershipChangeResponse)
def attach_documents(
    payload: GroupDocumentIdsRequest,
    group_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    """Attach already-uploaded documents to a group (idempotent per document).

    A newly attached document is a new research event, so auto research (when the
    deployment and the group both allow it) queues it for extraction right away.
    Existing memberships are reported as duplicates and never re-triggered.
    """
    service = ResearchGroupService(db)
    try:
        added, duplicates, missing, auto = service.attach_documents(
            group_id, payload.document_ids
        )
        group = service.get_group(group_id)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return GroupMembershipChangeResponse(
        added=added,
        duplicates=duplicates,
        missing=missing,
        group=_group_payload(group, service._counts(group.id)),
        auto_research=auto,
    )


@router.delete("/{group_id}/documents/{document_id}")
def detach_document(
    group_id: int = Path(..., ge=1),
    document_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    """Remove a document from the group (membership only; archival data stays)."""
    service = ResearchGroupService(db)
    try:
        service.detach_document(group_id, document_id)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except GroupDocumentNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return {"message": "Document removed from group", "group_id": group_id, "document_id": document_id}


@router.post("/{group_id}/import", response_model=ImportBatchResponse)
async def import_documents(
    group_id: int = Path(..., ge=1),
    files: List[UploadFile] = File(..., description="PDF or image files"),
    run_ocr: bool = Query(True, description="Queue OCR for the imported documents"),
    db: Session = Depends(get_db),
):
    """Batch-upload documents into a group (reuses the existing upload/OCR pipeline).

    Duplicate uploads are attached to the existing document instead of creating a
    second copy, and a duplicate membership is reported rather than inserted.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")
    payload = []
    for upload in files:
        name = (upload.filename or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="A file has no filename")
        if not name.lower().endswith(ACCEPTED_SUFFIXES):
            raise HTTPException(
                status_code=400,
                detail="Unsupported file type %r; accepted: %s"
                % (name, ", ".join(ACCEPTED_SUFFIXES)),
            )
        content = await upload.read()
        if len(content) > settings.max_upload_file_bytes:
            # Report the real limit instead of letting the browser fail while it
            # buffers a multipart body that is too large to send.
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="文件 %s 为 %.1f MB，超过单文件上限 %d MB。请拆分后再导入。"
                % (name, len(content) / (1024 * 1024), settings.max_upload_file_mb),
            )
        payload.append((name, content))

    service = ResearchGroupService(db)
    try:
        return service.import_documents(group_id, payload, run_ocr=run_ocr)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except SQLAlchemyError as exc:
        logger.error("Group import failed: %s", scrub_secrets(str(exc)))
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="导入失败：数据库不可用，请稍后重试。",
        )


@router.get("/{group_id}/imports/{batch_id}", response_model=ImportBatchStatusResponse)
def get_import_batch(
    group_id: int = Path(..., ge=1),
    batch_id: str = Path(..., min_length=4, max_length=64),
    db: Session = Depends(get_db),
):
    """Per-document status of one import batch."""
    service = ResearchGroupService(db)
    try:
        return service.batch_status(group_id, batch_id)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.post("/{group_id}/documents/{document_id}/retry", response_model=GroupMemberDocument)
def retry_document(
    group_id: int = Path(..., ge=1),
    document_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    """Re-queue OCR for one member document, reporting the failure reason."""
    service = ResearchGroupService(db)
    try:
        member = service.retry_document(group_id, document_id)
    except GroupNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except GroupDocumentNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    return GroupMemberDocument(**member)
