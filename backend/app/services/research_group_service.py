"""Research-group lifecycle and batch import.

Design notes
------------
* A group is a **membership list**, never a copy of archival data: detaching a
  document deletes one membership row and nothing else.
* Import reuses the existing upload + OCR + block pipeline
  (:class:`app.services.document_service.DocumentService` and ``run_ocr_task``);
  it does not introduce a second ingestion path. Word (``.docx``) files are the
  exception by nature: their text is read directly (see
  :mod:`app.services.docx_service`) and they never enter the OCR queue.
* De-duplication has an explicit identity rule: the SHA-256 of the uploaded bytes
  (``documents.content_hash``). Rows created before this feature are matched by
  ``(original_filename, file_size)`` and their hash is back-filled from the
  stored file, so the rule converges to content identity.
* A single failing file never invalidates the batch: each item is reported with
  its own status and error.
* The membership table has a unique constraint on ``(group_id, document_id)``,
  so duplicate memberships are impossible even under concurrent requests.
"""
import hashlib
import os
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import logger
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.research_group import GroupImportStatus, ResearchGroup, ResearchGroupDocument
from app.models.task import Task, TaskStatus, TaskType
from app.repositories import DocumentRepository, TaskRepository
from app.schemas.document import DocumentCreate
from app.schemas.research_group import (
    GroupCreate,
    GroupUpdate,
    ImportBatchItem,
    ImportBatchResponse,
    ImportBatchStatusResponse,
)
from app.schemas.task import TaskCreate
from app.services.docx_service import DocxParseError, create_docx_document
from app.services.group_scope import GroupNotFound
from app.services.rag.answer_integrity import scrub_secrets
from app.utils import is_docx, is_image, is_pdf, save_upload_file


class GroupConflict(Exception):
    """A group with the same name already exists (HTTP 409)."""


class GroupDocumentNotFound(Exception):
    """The membership (or the document) does not exist (HTTP 404)."""


def content_identity(content: bytes) -> str:
    """SHA-256 of the uploaded bytes."""
    return hashlib.sha256(content).hexdigest()


def derive_import_status(
    document_status: Optional[DocumentStatus],
    has_blocks: bool,
    latest_task_status: Optional[TaskStatus],
) -> Tuple[GroupImportStatus, Optional[str]]:
    """Pure mapping from the document/task state to the membership status.

    Text blocks win over a transient document flag: once the archival text is
    stored the document is searchable, which is what "ready" means for research.
    """
    if has_blocks:
        return GroupImportStatus.READY, None
    # The *latest* OCR attempt wins over the document's own flag: after a retry a
    # fresh queued task coexists with the previous attempt's FAILED document
    # status, and reporting that as failed would hide the successful re-queue.
    if latest_task_status in (TaskStatus.RUNNING,):
        return GroupImportStatus.PROCESSING, None
    if latest_task_status in (TaskStatus.PENDING,):
        return GroupImportStatus.QUEUED, None
    if document_status == DocumentStatus.FAILED:
        return GroupImportStatus.FAILED, None
    if latest_task_status in (TaskStatus.FAILED,):
        # A failed OCR task never yields archival text: report it instead of
        # falling through to "pending", which would look like a normal wait.
        return GroupImportStatus.FAILED, None
    if document_status in (DocumentStatus.PROCESSING, DocumentStatus.POLISHING):
        return GroupImportStatus.PROCESSING, None
    if document_status == DocumentStatus.OCR_COMPLETED:
        return GroupImportStatus.READY, None
    return GroupImportStatus.PENDING, None


def _default_enqueue(db: Session, document: Document) -> str:
    """Create an OCR task and hand it to Celery (the product pipeline).

    A broker rejection must not leave a "pending" task behind: that would make the
    membership look like a normal queued job. The task is marked failed and the
    exception is re-raised so the caller can record the real reason.
    """
    from app.tasks import run_ocr_task

    repository = TaskRepository(db)
    task = repository.create(TaskCreate(document_id=document.id, task_type=TaskType.OCR))
    try:
        celery_task = run_ocr_task.delay(task_id=task.id, document_id=document.id)
    except Exception as exc:  # noqa: BLE001 - the reason is reported to the caller
        repository.update_status(
            task.id,
            TaskStatus.FAILED,
            error_message=scrub_secrets("%s: %s" % (type(exc).__name__, exc))[:300],
        )
        raise
    repository.set_celery_id(task.id, celery_task.id)
    return str(task.id)


class ResearchGroupService:
    """CRUD for groups plus batch import into a group."""

    #: Injectable automatic-research trigger (tests replace it; production uses the
    #: module default that commits the run and then contacts the broker).
    auto_extract: Optional[Callable[..., object]] = None
    #: Injectable broker delivery for automatic runs.
    auto_dispatch: Optional[Callable[[int], None]] = None

    def __init__(self, db: Session):
        self.db = db

    def _hook(self, name: str):
        """Read an injectable hook without binding it to the instance.

        ``mock.patch.object(ResearchGroupService, "auto_dispatch", fn)`` stores a plain
        function on the class; ``self.auto_dispatch`` would bind ``self`` and call
        ``fn(self, run_id)``. Reading the class attribute directly avoids that.
        """
        for klass in type(self).__mro__:
            if name in klass.__dict__:
                return klass.__dict__[name]
        return None

    def _queue_auto_extraction(
        self, group: ResearchGroup, document: Document, trigger: str
    ):
        """Single hook for *all* import paths: a document became part of a group.

        Called only after the membership/pages/blocks are committed, so a run can
        never reference data that does not exist yet. Every failure is returned as an
        outcome instead of raising: auto research must never fail an import.
        """
        from app.services.kg import auto_extraction

        queue = self._hook("auto_extract") or auto_extraction.queue_document_extraction
        dispatch = self._hook("auto_dispatch")
        try:
            outcome = queue(
                self.db,
                group=group,
                document=document,
                trigger=trigger,
                dispatch=dispatch,
            )
        except Exception as exc:  # noqa: BLE001 - never break the import
            self.db.rollback()
            logger.error(
                "Auto extraction hook failed for document %s: %s", document.id, exc
            )
            return auto_extraction.AutoExtractionOutcome(
                started=False,
                reason=auto_extraction.REASON_ERROR,
                detail="自动研究触发失败：%s: %s" % (type(exc).__name__, exc),
                document_id=document.id,
            )
        logger.info(
            "Auto research for document %s (group %s): started=%s reason=%s run=%s",
            document.id,
            group.id,
            outcome.started,
            outcome.reason,
            outcome.run_id,
        )
        return outcome
    # ----------------------------------------------------------------- groups
    def list_groups(
        self, page: int = 1, page_size: int = 20, query: Optional[str] = None
    ) -> Tuple[List[Tuple[ResearchGroup, int, int, int]], int]:
        base = self.db.query(ResearchGroup)
        if query:
            base = base.filter(ResearchGroup.name.ilike("%%%s%%" % query.strip()))
        total = base.count()
        groups = (
            base.order_by(ResearchGroup.created_at.desc(), ResearchGroup.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return [(group, *self._counts(group.id)) for group in groups], total

    def _counts(self, group_id: int) -> Tuple[int, int, int]:
        members = (
            self.db.query(ResearchGroupDocument)
            .filter(ResearchGroupDocument.group_id == group_id)
            .all()
        )
        ready = sum(1 for m in members if m.import_status == GroupImportStatus.READY)
        failed = sum(1 for m in members if m.import_status == GroupImportStatus.FAILED)
        return len(members), ready, failed

    def get_group(self, group_id: int) -> ResearchGroup:
        group = self.db.query(ResearchGroup).filter(ResearchGroup.id == group_id).one_or_none()
        if group is None:
            raise GroupNotFound("文档组 #%s 不存在或无权访问。" % group_id)
        return group

    def create_group(self, payload: GroupCreate) -> ResearchGroup:
        name = payload.name.strip()
        if self.db.query(ResearchGroup).filter(ResearchGroup.name == name).first():
            raise GroupConflict("Research group %r already exists" % name)
        group = ResearchGroup(
            name=name,
            description=(payload.description or None),
            auto_extract=(
                True if payload.auto_extract is None else bool(payload.auto_extract)
            ),
        )
        self.db.add(group)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise GroupConflict("Research group %r already exists" % name) from None
        self.db.refresh(group)
        return group

    def update_group(self, group_id: int, payload: GroupUpdate) -> ResearchGroup:
        group = self.get_group(group_id)
        if payload.name is not None:
            name = payload.name.strip()
            clash = (
                self.db.query(ResearchGroup)
                .filter(ResearchGroup.name == name, ResearchGroup.id != group_id)
                .first()
            )
            if clash:
                raise GroupConflict("Research group %r already exists" % name)
            group.name = name
        if payload.description is not None:
            group.description = payload.description or None
        if payload.auto_extract is not None:
            # Toggling the switch only changes *future* events: it never scans the
            # group and never starts a run by itself.
            group.auto_extract = bool(payload.auto_extract)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise GroupConflict("Research group name already exists") from None
        self.db.refresh(group)
        return group

    def delete_group(self, group_id: int) -> None:
        """Delete the group and its memberships; documents remain untouched."""
        group = self.get_group(group_id)
        self.db.delete(group)
        self.db.commit()

    # ------------------------------------------------------------- membership
    def attach_documents(
        self, group_id: int, document_ids: Sequence[int], batch_id: Optional[str] = None
    ) -> Tuple[List[int], List[int], List[int], List[Dict[str, object]]]:
        """Attach existing documents. Returns (added, duplicates, missing, auto).

        A *new* membership is a real research event: when the deployment allows auto
        research, the attached document is queued for extraction. Documents that were
        already members are duplicates and never re-triggered.
        """
        group = self.get_group(group_id)
        added: List[int] = []
        duplicates: List[int] = []
        missing: List[int] = []
        auto_outcomes: List[Dict[str, object]] = []
        for document_id in dict.fromkeys(int(value) for value in document_ids):
            document = self.db.query(Document).filter(Document.id == document_id).one_or_none()
            if document is None:
                missing.append(document_id)
                continue
            if self._membership(group_id, document_id) is not None:
                duplicates.append(document_id)
                continue
            try:
                self.db.add(
                    ResearchGroupDocument(
                        group_id=group_id,
                        document_id=document_id,
                        import_status=self._initial_status(document_id),
                        last_batch_id=batch_id,
                    )
                )
                self.db.commit()
                added.append(document_id)
            except IntegrityError:
                self.db.rollback()
                duplicates.append(document_id)
                continue
            auto_outcomes.append(
                self._queue_auto_extraction(
                    group, document, "auto_attach"
                ).as_dict()
            )
        return added, duplicates, missing, auto_outcomes

    def _membership(self, group_id: int, document_id: int) -> Optional[ResearchGroupDocument]:
        return (
            self.db.query(ResearchGroupDocument)
            .filter(
                ResearchGroupDocument.group_id == group_id,
                ResearchGroupDocument.document_id == document_id,
            )
            .one_or_none()
        )

    def _initial_status(self, document_id: int) -> GroupImportStatus:
        has_blocks = self._has_blocks(document_id)
        document = self.db.query(Document).filter(Document.id == document_id).one_or_none()
        status, _ = derive_import_status(
            document.status if document else None, has_blocks, None
        )
        return status

    def _has_blocks(self, document_id: int) -> bool:
        from app.models.block import Block
        from app.models.page import Page

        return (
            self.db.query(Block.id)
            .join(Page, Page.id == Block.page_id)
            .filter(Page.document_id == document_id)
            .first()
            is not None
        )

    def detach_document(self, group_id: int, document_id: int) -> None:
        """Remove one membership row only (document and other groups untouched)."""
        self.get_group(group_id)
        membership = self._membership(group_id, document_id)
        if membership is None:
            raise GroupDocumentNotFound(
                "Document %s is not part of group %s" % (document_id, group_id)
            )
        self.db.delete(membership)
        self.db.commit()

    def list_members(self, group_id: int, refresh: bool = True) -> List[Dict[str, Any]]:
        """Group members with the real (derived) processing status."""
        self.get_group(group_id)
        rows = (
            self.db.query(ResearchGroupDocument)
            .filter(ResearchGroupDocument.group_id == group_id)
            .order_by(ResearchGroupDocument.added_at.asc(), ResearchGroupDocument.id.asc())
            .all()
        )
        if refresh:
            self.refresh_statuses(group_id, rows=rows)
        return [self._member_payload(row) for row in rows]

    def _member_payload(self, membership: ResearchGroupDocument) -> Dict[str, Any]:
        document = membership.document or (
            self.db.query(Document).filter(Document.id == membership.document_id).one_or_none()
        )
        return {
            "document_id": membership.document_id,
            "filename": document.filename if document else "(missing document)",
            "original_filename": document.original_filename if document else None,
            "file_type": str(getattr(document.file_type, "value", document.file_type))
            if document
            else "unknown",
            "file_size": document.file_size if document else 0,
            "page_count": document.page_count if document else 0,
            "block_count": self._block_count(membership.document_id),
            "document_status": str(getattr(document.status, "value", document.status))
            if document
            else "missing",
            "import_status": membership.import_status,
            "import_error": membership.import_error,
            "last_batch_id": membership.last_batch_id,
            "added_at": membership.added_at,
        }

    def _block_count(self, document_id: int) -> int:
        from app.models.block import Block
        from app.models.page import Page

        return (
            self.db.query(Block.id)
            .join(Page, Page.id == Block.page_id)
            .filter(Page.document_id == document_id)
            .count()
        )

    def refresh_statuses(
        self, group_id: int, rows: Optional[Iterable[ResearchGroupDocument]] = None
    ) -> int:
        """Recompute membership status from the document/task state; returns changes."""
        rows = list(
            rows
            if rows is not None
            else self.db.query(ResearchGroupDocument)
            .filter(ResearchGroupDocument.group_id == group_id)
            .all()
        )
        changed = 0
        for membership in rows:
            document = self.db.query(Document).filter(Document.id == membership.document_id).one_or_none()
            latest_task = (
                self.db.query(Task)
                .filter(Task.document_id == membership.document_id, Task.task_type == TaskType.OCR)
                .order_by(Task.created_at.desc(), Task.id.desc())
                .first()
            )
            status, _ = derive_import_status(
                document.status if document else None,
                self._has_blocks(membership.document_id),
                latest_task.status if latest_task else None,
            )
            if (
                status == GroupImportStatus.PENDING
                and membership.import_status == GroupImportStatus.FAILED
                and membership.import_error
            ):
                # A failure recorded at import time (e.g. the queue rejected the
                # job) leaves no task row, so the derived state looks "not
                # started". Nothing has changed since it failed: keep the failure
                # and its reason instead of hiding it behind "pending".
                continue
            error = None
            if status == GroupImportStatus.FAILED:
                recorded = membership.import_error if membership.import_status == status else None
                error = (
                    (document.error_message if document is not None else None)
                    or recorded
                    or "文档处理失败（未提供原因）"
                )[:500]
            if status != membership.import_status or (error or None) != (membership.import_error or None):
                membership.import_status = status
                membership.import_error = error
                changed += 1
        if changed:
            try:
                self.db.commit()
            except SQLAlchemyError:
                self.db.rollback()
                logger.error("Failed to refresh group %s member statuses", group_id)
                return 0
        return changed

    # ----------------------------------------------------------------- import
    def import_documents(
        self,
        group_id: int,
        files: Sequence[Tuple[str, bytes]],
        enqueue: Optional[Callable[[Session, Document], str]] = None,
        run_ocr: bool = True,
    ) -> ImportBatchResponse:
        """Upload + attach + (optionally) enqueue OCR for a batch of files.

        ``enqueue`` is injectable so tests and offline runs never need Celery.
        """
        group = self.get_group(group_id)
        batch_id = uuid.uuid4().hex
        enqueue_fn = enqueue if enqueue is not None else _default_enqueue

        items: List[ImportBatchItem] = []
        auto_outcomes: List[Dict[str, object]] = []
        accepted = duplicates = failed = 0

        for filename, content in files:
            try:
                digest = content_identity(content)
                document, created = self._resolve_document(filename, content, digest)
            except Exception as exc:  # noqa: BLE001 - one bad file must not kill the batch
                failed += 1
                items.append(
                    ImportBatchItem(
                        filename=filename,
                        error=scrub_secrets("%s: %s" % (type(exc).__name__, exc))[:300],
                        import_status=GroupImportStatus.FAILED,
                    )
                )
                continue

            if self._membership(group.id, document.id) is not None:
                duplicates += 1
                membership = self._membership(group.id, document.id)
                items.append(
                    ImportBatchItem(
                        filename=filename,
                        document_id=document.id,
                        duplicate=True,
                        import_status=membership.import_status if membership else None,
                    )
                )
                continue

            membership = ResearchGroupDocument(
                group_id=group.id,
                document_id=document.id,
                import_status=GroupImportStatus.PENDING,
                last_batch_id=batch_id,
            )
            self.db.add(membership)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                duplicates += 1
                items.append(
                    ImportBatchItem(filename=filename, document_id=document.id, duplicate=True)
                )
                continue

            status = GroupImportStatus.PENDING
            error = None
            if document.file_type == DocumentType.DOCX:
                # Word text is stored during import, so the document is
                # searchable immediately: no OCR task is created or queued.
                status = GroupImportStatus.READY
            elif run_ocr:
                try:
                    enqueue_fn(self.db, document)
                    status = GroupImportStatus.QUEUED
                except Exception as exc:  # noqa: BLE001 - queue failure is per-document
                    status = GroupImportStatus.FAILED
                    error = scrub_secrets("%s: %s" % (type(exc).__name__, exc))[:300]
            try:
                membership.import_status = status
                membership.import_error = error
                self.db.commit()
            except SQLAlchemyError:
                self.db.rollback()
                logger.error("Failed to set import status for document %s", document.id)

            # Auto research: only after this document's text and membership are
            # committed, and only for the document that actually succeeded.
            auto_outcome = None
            if status == GroupImportStatus.READY:
                auto_outcome = self._queue_auto_extraction(
                    group, document, "auto_import"
                )
                auto_outcomes.append(auto_outcome.as_dict())

            accepted += 1
            items.append(
                ImportBatchItem(
                    filename=filename,
                    document_id=document.id,
                    created_document=created,
                    import_status=status,
                    error=error,
                    auto_research=auto_outcome.as_dict() if auto_outcome else None,
                )
            )

        self.refresh_statuses(group_id)
        return ImportBatchResponse(
            batch_id=batch_id,
            group_id=group.id,
            accepted_count=accepted,
            duplicate_count=duplicates,
            failed_count=failed,
            items=items,
            auto_research=auto_outcomes,
        )

    def _resolve_document(
        self, filename: str, content: bytes, digest: str
    ) -> Tuple[Document, bool]:
        """Find the document with this content identity, or create it.

        This reuses the existing upload path (the same file-type validation and
        storage helper as ``POST /documents/upload``) without going through the
        async HTTP layer, so it can be called from the batch loop.
        """
        existing = (
            self.db.query(Document).filter(Document.content_hash == digest).first()
        )
        if existing is not None:
            return existing, False

        for candidate in (
            self.db.query(Document)
            .filter(
                Document.content_hash.is_(None),
                Document.original_filename == filename,
                Document.file_size == len(content),
            )
            .all()
        ):
            if self._backfill_hash(candidate) == digest:
                return candidate, False

        if is_docx(filename):
            document = create_docx_document(self.db, filename, content)
            document.content_hash = digest
            self.db.commit()
            return document, True

        if not (is_pdf(filename) or is_image(filename)):
            raise ValueError("Unsupported file type: %s" % filename)

        file_type = DocumentType.PDF if is_pdf(filename) else DocumentType.IMAGE
        stored_path = save_upload_file(content, filename)
        document = DocumentRepository(self.db).create(
            DocumentCreate(
                filename=stored_path.name,
                original_filename=filename,
                file_path=str(stored_path),
                file_type=file_type,
                file_size=len(content),
            )
        )
        document.content_hash = digest
        try:
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            raise
        self.db.refresh(document)
        return document, True

    def _backfill_hash(self, document: Document) -> Optional[str]:
        path = document.file_path
        if not path:
            return None
        candidates = [path, os.path.join("..", path), os.path.join("..", "..", path)]
        for candidate in candidates:
            try:
                if os.path.isfile(candidate):
                    with open(candidate, "rb") as handle:
                        digest = hashlib.sha256(handle.read()).hexdigest()
                    document.content_hash = digest
                    self.db.commit()
                    return digest
            except OSError:
                continue
        return None

    def retry_document(
        self,
        group_id: int,
        document_id: int,
        enqueue: Optional[Callable[[Session, Document], str]] = None,
    ) -> Dict[str, Any]:
        """Re-enqueue OCR for a failed member (idempotent at the membership level)."""
        self.get_group(group_id)
        membership = self._membership(group_id, document_id)
        if membership is None:
            raise GroupDocumentNotFound(
                "Document %s is not part of group %s" % (document_id, group_id)
            )
        document = self.db.query(Document).filter(Document.id == document_id).one_or_none()
        if document is None:
            raise GroupDocumentNotFound("Document %s not found" % document_id)

        enqueue_fn = enqueue if enqueue is not None else _default_enqueue
        try:
            enqueue_fn(self.db, document)
            membership.import_status = GroupImportStatus.QUEUED
            membership.import_error = None
        except Exception as exc:  # noqa: BLE001 - keep the reason visible
            membership.import_status = GroupImportStatus.FAILED
            membership.import_error = scrub_secrets(
                "%s: %s" % (type(exc).__name__, exc)
            )[:300]
        try:
            self.db.commit()
        except SQLAlchemyError:
            self.db.rollback()
            raise
        return self._member_payload(membership)

    def batch_status(self, group_id: int, batch_id: str) -> ImportBatchStatusResponse:
        self.get_group(group_id)
        rows = (
            self.db.query(ResearchGroupDocument)
            .filter(
                ResearchGroupDocument.group_id == group_id,
                ResearchGroupDocument.last_batch_id == batch_id,
            )
            .order_by(ResearchGroupDocument.id.asc())
            .all()
        )
        self.refresh_statuses(group_id, rows=rows)
        items = [
            ImportBatchItem(
                filename=(row.document.original_filename if row.document else "(missing document)")
                or "(unknown file)",
                document_id=row.document_id,
                import_status=row.import_status,
                error=row.import_error,
            )
            for row in rows
        ]
        counts: Dict[str, int] = {}
        for item in items:
            key = str(getattr(item.import_status, "value", item.import_status))
            counts[key] = counts.get(key, 0) + 1
        return ImportBatchStatusResponse(
            batch_id=batch_id,
            group_id=group_id,
            total=len(items),
            status_counts=counts,
            items=items,
        )


research_group_service_class = ResearchGroupService
