"""Automatic knowledge-graph research after a successful import.

One job: turn a *real* import event into a persistent, budgeted extraction run, and
make sure the queue really received it. Nothing else in the system may call the
model as a side effect of importing a file.

Design rules
------------
* **Event driven, never state driven.** Runs are created only when a document is
  successfully imported into a group or newly attached to one. Enabling the switch,
  restarting the service or opening a group page must not start anything: an
  existing group with thousands of unextracted blocks stays untouched until a human
  asks for the backfill run (``POST /api/kg/groups/{id}/extraction``).
* **One run per document.** Concurrent imports therefore never collide on a group
  level lock, and a run that is already active for the *same* document is simply
  reported as ``already_active`` (the document is not lost, it is in the queue).
* **Persisted before delivered.** The run row is committed first; the broker is
  contacted afterwards. A delivery failure marks ``dispatch_state='failed'`` with a
  reason and keeps the row, so it can be re-delivered.
* **Idempotent by data, not by hope.** The block-level ledger already skips blocks
  that carry mentions, so a re-delivered or repeated run cannot duplicate evidence.
* **Extraction is never acceptance.** Runs produce provisional persons and
  asserted relationships; review stays a separate human step.
"""
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from sqlalchemy import func, or_, and_
from sqlalchemy.orm import Session

from app.config import settings
from app.core import logger
from app.models.block import Block
from app.models.document import Document
from app.models.kg.extraction_run import (
    KGExtractionDispatchState,
    KGExtractionRun,
    KGExtractionRunStatus,
    KGExtractionRunTrigger,
)
from app.models.kg.mention import PersonMention
from app.models.page import Page
from app.models.research_group import ResearchGroup
from app.services.kg import extraction_run_service

#: Reason codes reported to the API/UI. They explain *why* nothing was queued.
REASON_STARTED = "started"
REASON_ALREADY_ACTIVE = "already_active"
REASON_ALREADY_EXTRACTED = "already_extracted"
REASON_GLOBAL_DISABLED = "global_disabled"
REASON_GROUP_DISABLED = "group_disabled"
REASON_MODEL_NOT_CONFIGURED = "model_not_configured"
REASON_NO_TEXT = "no_text"
REASON_CONFLICT = "conflict"
REASON_ERROR = "error"

_ACTIVE_STATUSES = (KGExtractionRunStatus.QUEUED.value, KGExtractionRunStatus.RUNNING.value)

#: Injectable broker delivery (tests replace this; it must never run inside the
#: request's transaction, which is why it is called after the commit).
Dispatcher = Callable[[int], None]


def _default_dispatch(run_id: int) -> None:
    """Hand the run to Celery. Raises when the broker is unreachable."""
    from app.tasks.kg_tasks import run_kg_extraction_batches_task

    run_kg_extraction_batches_task.delay(run_id)


@dataclass(frozen=True)
class AutoResearchStatus:
    """Everything the UI needs to explain whether automatic research is active."""

    global_enabled: bool
    group_enabled: bool
    model_configured: bool
    budget_blocks: int
    budget_tokens: Optional[int]
    model: str
    effective: bool
    reason: str
    detail: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "global_enabled": self.global_enabled,
            "group_enabled": self.group_enabled,
            "model_configured": self.model_configured,
            "budget_blocks": self.budget_blocks,
            "budget_tokens": self.budget_tokens,
            "model": self.model,
            "effective": self.effective,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class AutoExtractionOutcome:
    """Result of trying to queue one document."""

    started: bool
    reason: str
    detail: str
    document_id: Optional[int] = None
    run_id: Optional[int] = None
    dispatch_state: Optional[str] = None
    dispatch_error: Optional[str] = None
    total_blocks: int = 0

    def as_dict(self) -> Dict[str, object]:
        return {
            "document_id": self.document_id,
            "started": self.started,
            "reason": self.reason,
            "detail": self.detail,
            "run_id": self.run_id,
            "dispatch_state": self.dispatch_state,
            "dispatch_error": self.dispatch_error,
            "total_blocks": self.total_blocks,
        }


def model_configured() -> bool:
    """A real model connection must exist before any automatic call is queued."""
    return bool((settings.gpt_api_key or "").strip()) and bool(
        (settings.gpt_base_url or "").strip()
    )


def auto_research_status(group: Optional[ResearchGroup]) -> AutoResearchStatus:
    """Explain the *current* configuration for one group (no database writes)."""
    global_enabled = bool(settings.kg_auto_extract_on_import)
    group_enabled = bool(getattr(group, "auto_extract", True)) if group is not None else False
    configured = model_configured()
    budget_blocks = int(settings.kg_auto_extract_budget_blocks or 0)
    budget_tokens = int(settings.kg_auto_extract_budget_tokens or 0) or None
    model = settings.gpt_model

    if group is None:
        return AutoResearchStatus(
            global_enabled=global_enabled,
            group_enabled=False,
            model_configured=configured,
            budget_blocks=budget_blocks,
            budget_tokens=budget_tokens,
            model=model,
            effective=False,
            reason=REASON_GROUP_DISABLED,
            detail="全局视图不属于任何文档组，自动研究只对文档组生效。",
        )
    if not global_enabled:
        return AutoResearchStatus(
            global_enabled=False,
            group_enabled=group_enabled,
            model_configured=configured,
            budget_blocks=budget_blocks,
            budget_tokens=budget_tokens,
            model=model,
            effective=False,
            reason=REASON_GLOBAL_DISABLED,
            detail=(
                "部署级开关 KG_AUTO_EXTRACT_ON_IMPORT 未启用：导入不会自动排队，"
                "仍可在组页面手动启动抽取。"
            ),
        )
    if not group_enabled:
        return AutoResearchStatus(
            global_enabled=True,
            group_enabled=False,
            model_configured=configured,
            budget_blocks=budget_blocks,
            budget_tokens=budget_tokens,
            model=model,
            effective=False,
            reason=REASON_GROUP_DISABLED,
            detail="该文档组已关闭自动研究：新导入的文档不会自动排队。",
        )
    if not configured:
        return AutoResearchStatus(
            global_enabled=True,
            group_enabled=True,
            model_configured=False,
            budget_blocks=budget_blocks,
            budget_tokens=budget_tokens,
            model=model,
            effective=False,
            reason=REASON_MODEL_NOT_CONFIGURED,
            detail=(
                "自动研究已开启，但未配置模型连接（GPT_API_KEY / GPT_BASE_URL），"
                "因此不会发起任何模型调用。"
            ),
        )
    return AutoResearchStatus(
        global_enabled=True,
        group_enabled=True,
        model_configured=True,
        budget_blocks=budget_blocks,
        budget_tokens=budget_tokens,
        model=model,
        effective=True,
        reason=REASON_STARTED,
        detail=(
            "自动研究已启用：新导入/新加入本组的 Word 文档会自动排队抽取，"
            "单次任务上限 %d 个区块。" % budget_blocks
            if budget_blocks
            else "自动研究已启用：新导入/新加入本组的 Word 文档会自动排队抽取。"
        ),
    )


def _blocks_with_text(db: Session, document_id: int) -> int:
    return (
        db.query(func.count(Block.id))
        .join(Page, Page.id == Block.page_id)
        .filter(
            Page.document_id == document_id,
            or_(
                and_(Block.ocr_text_corrected.isnot(None), Block.ocr_text_corrected != ""),
                and_(Block.ocr_text_raw.isnot(None), Block.ocr_text_raw != ""),
            ),
        )
        .scalar()
        or 0
    )


def _already_extracted(db: Session, document_id: int) -> bool:
    """True when every text block of the document already carries mentions."""
    total = _blocks_with_text(db, document_id)
    if total == 0:
        return False
    done = (
        db.query(func.count(func.distinct(PersonMention.block_id)))
        .join(Block, Block.id == PersonMention.block_id)
        .join(Page, Page.id == Block.page_id)
        .filter(Page.document_id == document_id)
        .scalar()
        or 0
    )
    return done >= total


def dispatch_run(
    db: Session,
    run: KGExtractionRun,
    dispatch: Optional[Dispatcher] = None,
) -> Tuple[str, Optional[str]]:
    """Deliver an already committed run. Never raises: failures are recorded."""
    deliver = dispatch or _default_dispatch
    run.dispatch_attempts = (run.dispatch_attempts or 0) + 1
    try:
        deliver(run.id)
    except Exception as exc:  # noqa: BLE001 - the reason must be visible, not raised
        run.dispatch_state = KGExtractionDispatchState.FAILED.value
        run.dispatch_error = ("%s: %s" % (type(exc).__name__, exc))[:500]
        db.commit()
        logger.error(
            "KG extraction run %s could not be dispatched: %s", run.id, run.dispatch_error
        )
        return run.dispatch_state, run.dispatch_error
    run.dispatch_state = KGExtractionDispatchState.DISPATCHED.value
    run.dispatch_error = None
    db.commit()
    logger.info("KG extraction run %s dispatched to the queue", run.id)
    return run.dispatch_state, None


def queue_document_extraction(
    db: Session,
    *,
    group: ResearchGroup,
    document: Document,
    trigger: str = KGExtractionRunTrigger.AUTO_IMPORT.value,
    dispatch: Optional[Dispatcher] = None,
) -> AutoExtractionOutcome:
    """Create (and deliver) a document-scoped run for a freshly imported document.

    Returns an outcome instead of raising: a single document that cannot be queued
    must not turn a successful import into a failure.
    """
    status = auto_research_status(group)
    if not status.effective:
        return AutoExtractionOutcome(
            started=False, reason=status.reason, detail=status.detail, document_id=document.id
        )

    blocks = _blocks_with_text(db, document.id)
    if blocks == 0:
        return AutoExtractionOutcome(
            started=False,
            reason=REASON_NO_TEXT,
            detail="该文档没有可抽取的文本区块，未创建抽取任务。",
            document_id=document.id,
        )
    if _already_extracted(db, document.id):
        return AutoExtractionOutcome(
            started=False,
            reason=REASON_ALREADY_EXTRACTED,
            detail="该文档的全部文本区块已有抽取结果，本次未重复排队。",
            document_id=document.id,
        )

    active = (
        db.query(KGExtractionRun)
        .filter(
            KGExtractionRun.document_id == document.id,
            KGExtractionRun.status.in_(_ACTIVE_STATUSES),
        )
        .order_by(KGExtractionRun.id.desc())
        .first()
    )
    if active is not None:
        if active.dispatch_state == KGExtractionDispatchState.FAILED.value:
            # The document is queued but the broker never received it: re-deliver
            # instead of stacking a second run.
            state, error = dispatch_run(db, active, dispatch)
            return AutoExtractionOutcome(
                started=True,
                reason=REASON_ALREADY_ACTIVE,
                detail="该文档已有抽取任务，但上次投递失败，已重新投递。",
                document_id=document.id,
                run_id=active.id,
                dispatch_state=state,
                dispatch_error=error,
                total_blocks=active.total_blocks,
            )
        return AutoExtractionOutcome(
            started=False,
            reason=REASON_ALREADY_ACTIVE,
            detail="该文档已有排队或运行中的抽取任务，本次未重复创建。",
            document_id=document.id,
            run_id=active.id,
            dispatch_state=active.dispatch_state,
            total_blocks=active.total_blocks,
        )

    try:
        run = extraction_run_service.create_run(
            db,
            group_id=group.id,
            document_id=document.id,
            budget_blocks=status.budget_blocks or None,
            budget_tokens=status.budget_tokens,
            max_consecutive_failures=int(
                settings.kg_auto_extract_max_consecutive_failures or 3
            ),
            trigger=trigger,
            budget_source="auto",
        )
    except extraction_run_service.RunConflict as exc:
        return AutoExtractionOutcome(
            started=False,
            reason=REASON_CONFLICT,
            detail="该文档已有进行中的抽取任务：%s" % exc,
            document_id=document.id,
        )
    except Exception as exc:  # noqa: BLE001 - import must still succeed
        db.rollback()
        logger.error(
            "Auto extraction could not be queued for document %s: %s",
            document.id,
            exc,
        )
        return AutoExtractionOutcome(
            started=False,
            reason=REASON_ERROR,
            detail="创建抽取任务失败：%s: %s" % (type(exc).__name__, exc),
            document_id=document.id,
        )

    state, error = dispatch_run(db, run, dispatch)
    return AutoExtractionOutcome(
        started=True,
        reason=REASON_STARTED,
        detail=(
            "已自动创建抽取任务（%d 个文本区块）。" % run.total_blocks
            if not error
            else "已登记抽取任务，但队列投递失败：%s" % error
        ),
        document_id=document.id,
        run_id=run.id,
        dispatch_state=state,
        dispatch_error=error,
        total_blocks=run.total_blocks,
    )


def redispatch_run(
    db: Session, run_id: int, dispatch: Optional[Dispatcher] = None
) -> Dict[str, object]:
    """Re-deliver a run whose message was lost; safe because blocks are logged."""
    run = db.query(KGExtractionRun).filter(KGExtractionRun.id == run_id).one_or_none()
    if run is None:
        raise extraction_run_service.RunNotFound("抽取任务 #%s 不存在。" % run_id)
    if run.status not in _ACTIVE_STATUSES:
        raise extraction_run_service.RunStateError(
            "当前状态（%s）不需要重新投递：任务已停止，请恢复后再投递。" % run.status
        )
    state, error = dispatch_run(db, run, dispatch)
    snapshot = extraction_run_service.run_status(db, run)
    snapshot["dispatch_state"] = state
    snapshot["dispatch_error"] = error
    return snapshot
