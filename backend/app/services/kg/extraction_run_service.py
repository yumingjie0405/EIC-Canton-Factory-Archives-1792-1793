"""Resumable, budget-aware KG extraction over a document or a whole group.

Design rules enforced here
--------------------------
* **One LLM call per block.** Page/document/group levels only orchestrate; the atomic
  unit that touches the model is a block, so retries can never re-extract a whole book.
* **Persistent progress.** Every block outcome is written to ``kg_extraction_items``;
  a restart continues from the ledger instead of from memory.
* **Idempotent by content, not by click.** A block already carrying mentions for the
  same model + prompt is ``skipped`` unless ``overwrite`` is passed explicitly.
* **Stop instead of burning budget.** Block/token ceilings and repeated failures pause
  the run with a reason; only an explicit resume continues it.
* **Extraction produces candidates, never accepted facts.** ``kg_writer`` creates
  provisional persons and asserted relationships; acceptance stays a human action.
"""
import asyncio
import contextvars
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.core import logger
from app.models.block import Block
from app.models.document import Document
from app.models.kg.extraction_run import (
    KGExtractionDispatchState,
    KGExtractionItem,
    KGExtractionItemStatus,
    KGExtractionRun,
    KGExtractionRunStatus,
)
from app.models.kg.mention import PersonMention
from app.models.page import Page
from app.models.research_group import ResearchGroupDocument
from app.services.group_scope import GroupNotFound
from app.services.kg import provenance
from app.services.kg.llm_client import kg_llm_client

ACTIVE_STATUSES = (
    KGExtractionRunStatus.QUEUED.value,
    KGExtractionRunStatus.RUNNING.value,
)
PAUSED_STATUSES = (
    KGExtractionRunStatus.PAUSED_BUDGET.value,
    KGExtractionRunStatus.PAUSED_ERROR.value,
    KGExtractionRunStatus.PAUSED_FORMAT.value,
    KGExtractionRunStatus.PAUSED_MANUAL.value,
)
TERMINAL_STATUSES = (
    KGExtractionRunStatus.COMPLETED.value,
    KGExtractionRunStatus.PARTIAL.value,
    KGExtractionRunStatus.FAILED.value,
)

#: Exceptions that usually mean "the provider/network is unhappy", not "this text
#: cannot be parsed". They are retried (with the consecutive-failure pause as a brake).
TRANSIENT_ERROR_HINTS = (
    "connection",
    "timeout",
    "timed out",
    "temporarily",
    "rate limit",
    "429",
    "502",
    "503",
    "504",
    "unavailable",
)


class RunConflict(Exception):
    """An active run already exists for this scope (HTTP 409)."""


class RunNotFound(Exception):
    """Unknown run id (HTTP 404)."""


class RunStateError(Exception):
    """The requested transition is not allowed in the current state (HTTP 409)."""


def _is_transient(exc: BaseException) -> bool:
    text = ("%s %s" % (type(exc).__name__, exc)).lower()
    return any(hint in text for hint in TRANSIENT_ERROR_HINTS)


class BudgetError(ValueError):
    """The requested extraction budget is missing, illegal or above the ceiling."""


#: The run whose context the block extractor should stamp onto new records. A
#: contextvar keeps the ``(db, block_id)`` extractor signature (test doubles rely on it)
#: while still letting the writer record which run produced a mention or an evidence.
_current_run: "contextvars.ContextVar[Optional[KGExtractionRun]]" = contextvars.ContextVar(
    "kg_current_run", default=None
)


def prompt_fingerprint() -> str:
    """Hash of the prompt template and the extraction configuration.

    Recorded on every run so "same model, different prompt" is a different
    configuration: it must invalidate earlier results instead of reusing them.
    """
    import hashlib
    from pathlib import Path

    template_path = Path(__file__).resolve().parent.parent.parent / "prompts" / "kg_extraction_prompt.txt"
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError:
        template = "kg-extraction-default-system-prompt"
    config_bits = "|".join(
        str(value)
        for value in (
            settings.gpt_model,
            settings.gpt_page_max_tokens,
            settings.gpt_structured_output,
            settings.gpt_page_enable_thinking,
        )
    )
    return hashlib.sha256((template + "\n" + config_bits).encode("utf-8")).hexdigest()[:64]


def resolve_budget(
    budget_blocks: Optional[int],
    budget_tokens: Optional[int],
    budget_source: Optional[str] = None,
) -> Tuple[int, Optional[int], str]:
    """Return a valid ``(blocks, tokens, source)`` budget or raise.

    Rules (deliberately strict, because an unbounded run over a large group is an
    unbounded bill):

    * missing ``budget_blocks`` -> the conservative deployment default;
    * ``<= 0`` or not an integer -> refused, never silently replaced;
    * above the hard ceiling -> refused, never silently clamped;
    * tokens are optional, but when supplied they must be positive and under their
      own ceiling.
    """
    max_blocks = int(settings.kg_extraction_max_budget_blocks or 0)
    if max_blocks <= 0:
        raise BudgetError("未配置单次抽取区块上限（KG_EXTRACTION_MAX_BUDGET_BLOCKS），拒绝启动任务。")

    source = budget_source or ("explicit" if budget_blocks is not None else "default")
    if budget_blocks is None:
        default_blocks = int(settings.kg_extraction_default_budget_blocks or 0)
        if default_blocks <= 0:
            raise BudgetError(
                "未配置默认抽取区块预算（KG_EXTRACTION_DEFAULT_BUDGET_BLOCKS），拒绝启动任务。"
            )
        blocks = default_blocks
        source = budget_source or "default"
    else:
        if isinstance(budget_blocks, bool) or not isinstance(budget_blocks, int):
            raise BudgetError("区块预算必须是整数。")
        if budget_blocks <= 0:
            raise BudgetError("区块预算必须大于 0。")
        if budget_blocks > max_blocks:
            raise BudgetError(
                "区块预算 %d 超过单次上限 %d，请拆分为多次任务。" % (budget_blocks, max_blocks)
            )
        blocks = budget_blocks

    max_tokens = int(settings.kg_extraction_max_budget_tokens or 0)
    if budget_tokens is None or budget_tokens == 0:
        tokens = None
    else:
        if isinstance(budget_tokens, bool) or not isinstance(budget_tokens, int):
            raise BudgetError("token 预算必须是整数。")
        if budget_tokens <= 0:
            raise BudgetError("token 预算必须大于 0。")
        if max_tokens and budget_tokens > max_tokens:
            raise BudgetError(
                "token 预算 %d 超过单次上限 %d。" % (budget_tokens, max_tokens)
            )
        tokens = budget_tokens
    return blocks, tokens, source


def _scope_block_query(db: Session, *, group_id: Optional[int], document_id: Optional[int]):
    """Blocks with usable text inside the requested scope."""
    query = db.query(Block.id).join(Page, Page.id == Block.page_id)
    if document_id is not None:
        query = query.filter(Page.document_id == document_id)
    elif group_id is not None:
        document_ids = [
            row[0]
            for row in db.query(ResearchGroupDocument.document_id).filter(
                ResearchGroupDocument.group_id == group_id
            )
        ]
        if not document_ids:
            return None
        query = query.filter(Page.document_id.in_(document_ids))
    query = query.filter(
        or_(
            and_(Block.ocr_text_corrected.isnot(None), Block.ocr_text_corrected != ""),
            and_(Block.ocr_text_raw.isnot(None), Block.ocr_text_raw != ""),
        )
    )
    return query


def create_run(
    db: Session,
    *,
    group_id: Optional[int] = None,
    document_id: Optional[int] = None,
    budget_blocks: Optional[int] = None,
    budget_tokens: Optional[int] = None,
    max_consecutive_failures: int = 3,
    model: Optional[str] = None,
    prompt_hash: Optional[str] = None,
    trigger: str = "manual",
    budget_source: Optional[str] = None,
) -> KGExtractionRun:
    """Create a queued run for one document or one group.

    Extraction is never implicit, and it is **never unbounded**: a missing budget
    falls back to the conservative deployment default, and a request above the hard
    ceiling is refused instead of being silently clamped.
    """
    if group_id is None and document_id is None:
        raise ValueError("either group_id or document_id is required")

    budget_blocks, tokens, source = resolve_budget(
        budget_blocks, budget_tokens, budget_source
    )

    if document_id is not None:
        document = db.query(Document).filter(Document.id == document_id).one_or_none()
        if document is None:
            raise ValueError("文档 #%s 不存在。" % document_id)
        if group_id is not None:
            member = (
                db.query(ResearchGroupDocument)
                .filter(
                    ResearchGroupDocument.group_id == group_id,
                    ResearchGroupDocument.document_id == document_id,
                )
                .one_or_none()
            )
            if member is None:
                raise GroupNotFound("文档 #%s 不属于文档组 #%s。" % (document_id, group_id))

    if group_id is not None:
        from app.models.research_group import ResearchGroup

        group = db.query(ResearchGroup).filter(ResearchGroup.id == group_id).one_or_none()
        if group is None:
            raise GroupNotFound("文档组 #%s 不存在或无权访问。" % group_id)

    scope_type = "document" if document_id is not None else "group"
    scope_id = document_id if document_id is not None else group_id

    existing = (
        db.query(KGExtractionRun)
        .filter(
            KGExtractionRun.scope_type == scope_type,
            KGExtractionRun.scope_id == scope_id,
            KGExtractionRun.status.in_(ACTIVE_STATUSES),
        )
        .first()
    )
    if existing is not None:
        raise RunConflict(
            "该范围已有进行中的抽取任务（run #%s，状态 %s），请等待完成或先暂停。"
            % (existing.id, existing.status)
        )

    block_query = _scope_block_query(db, group_id=group_id, document_id=document_id)
    total = 0 if block_query is None else block_query.count()

    resolved_model = model or kg_llm_client.default_model
    source_kind = provenance.classify_model(resolved_model)

    run = KGExtractionRun(
        scope_type=scope_type,
        scope_id=scope_id,
        group_id=group_id,
        document_id=document_id,
        status=KGExtractionRunStatus.QUEUED.value,
        model=resolved_model,
        prompt_hash=prompt_hash or prompt_fingerprint(),
        trigger=trigger,
        dispatch_state=KGExtractionDispatchState.PENDING.value,
        total_blocks=total,
        budget_blocks=budget_blocks,
        budget_tokens=tokens,
        budget_source=source,
        source_kind=source_kind,
        token_source=provenance.token_source_for(source_kind, 0),
        max_consecutive_failures=max_consecutive_failures,
    )
    db.add(run)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise RunConflict("该范围已有进行中的抽取任务。") from None
    db.refresh(run)
    logger.info(
        "KG extraction run created: id=%s scope=%s:%s blocks=%s",
        run.id,
        scope_type,
        scope_id,
        total,
    )
    return run


def _pending_block_ids(db: Session, run: KGExtractionRun, limit: int):
    done = db.query(KGExtractionItem.block_id).filter(KGExtractionItem.run_id == run.id)
    query = _scope_block_query(db, group_id=run.group_id, document_id=run.document_id)
    if query is None:
        return []
    query = query.filter(~Block.id.in_(done))
    if run.cursor_block_id is not None:
        query = query.filter(Block.id > run.cursor_block_id)
    return [row[0] for row in query.order_by(Block.id.asc()).limit(limit).all()]


def _has_existing_extraction(db: Session, block_id: int, run: KGExtractionRun) -> bool:
    """Was this block already extracted *by the same model + prompt*?

    The ledger is the only place that records which configuration produced a block's
    result, so it decides idempotency: a block that succeeded under the same model (and
    the same prompt hash, when both runs know it) is skipped. A run with a different
    model or prompt must be allowed to re-extract, otherwise a model/config change would
    silently reuse results the current configuration never produced.

    Mentions written before the ledger existed cannot be attributed to any
    configuration; those blocks are treated as already extracted (never re-billed).
    """
    matching = (
        db.query(KGExtractionItem.id)
        .join(KGExtractionRun, KGExtractionRun.id == KGExtractionItem.run_id)
        .filter(
            KGExtractionItem.block_id == block_id,
            KGExtractionItem.status == KGExtractionItemStatus.SUCCEEDED.value,
            KGExtractionRun.model == run.model,
        )
    )
    if run.prompt_hash:
        matching = matching.filter(KGExtractionRun.prompt_hash == run.prompt_hash)
    if matching.first() is not None:
        return True

    has_mentions = (
        db.query(PersonMention.id).filter(PersonMention.block_id == block_id).first() is not None
    )
    if not has_mentions:
        return False
    has_ledger_item = (
        db.query(KGExtractionItem.id).filter(KGExtractionItem.block_id == block_id).first()
        is not None
    )
    return not has_ledger_item


def _default_extractor(db: Session, block_id: int) -> object:
    """Run the async extraction service in a private loop (Celery-worker friendly)."""
    from app.services.kg.extraction_service import extraction_service

    run = _current_run.get()
    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(
            extraction_service.extract_from_block(
                db=db,
                block_id=block_id,
                overwrite_existing=False,
                run_id=run.id if run is not None else None,
                source_kind=run.source_kind if run is not None else None,
            )
        )
    finally:
        loop.close()


def _finish_if_done(db: Session, run: KGExtractionRun) -> None:
    """Mark a run finished only if it is still active and has no pending blocks."""
    if run.status not in ACTIVE_STATUSES:
        # A pause (budget / failure) must win over "no blocks left": otherwise the run
        # would report success while the reason it stopped would be lost.
        return
    remaining = _pending_block_ids(db, run, limit=1)
    if remaining:
        return
    run.status = (
        KGExtractionRunStatus.COMPLETED.value
        if run.failed_blocks == 0
        else KGExtractionRunStatus.PARTIAL.value
    )
    run.finished_at = datetime.utcnow()
    db.commit()


def process_batch(
    db: Session,
    run_id: int,
    limit: int = 20,
    extractor: Optional[Callable[[Session, int], object]] = None,
    *,
    overwrite_existing: bool = False,
) -> Dict[str, object]:
    """Process up to ``limit`` pending blocks; returns the run status snapshot.

    The extractor is injectable so tests (and offline pilots) never need a model.
    """
    run = db.query(KGExtractionRun).filter(KGExtractionRun.id == run_id).one_or_none()
    if run is None:
        raise RunNotFound("抽取任务 #%s 不存在。" % run_id)

    if run.status in TERMINAL_STATUSES or run.status in PAUSED_STATUSES:
        return run_status(db, run)

    if run.status == KGExtractionRunStatus.QUEUED.value:
        run.status = KGExtractionRunStatus.RUNNING.value
        run.started_at = run.started_at or datetime.utcnow()
        db.commit()

    extract = extractor or _default_extractor
    run_token = _current_run.set(run)
    try:
        return _process_pending(db, run, limit, extract, overwrite_existing)
    finally:
        _current_run.reset(run_token)


def _process_pending(
    db: Session,
    run: KGExtractionRun,
    limit: int,
    extract: Callable[[Session, int], object],
    overwrite_existing: bool,
) -> Dict[str, object]:
    for block_id in _pending_block_ids(db, run, limit):
        # Honour a pause/resume/cancel issued through another session. Without this
        # check the worker kept extracting after "pause" and each per-block commit even
        # reset the status back to "running", so the operator's stop was silently lost
        # (a real run billed ~2.3 M tokens while marked paused).
        current_status = (
            db.query(KGExtractionRun.status)
            .filter(KGExtractionRun.id == run.id)
            .scalar()
        )
        if current_status is not None and (
            current_status in PAUSED_STATUSES or current_status in TERMINAL_STATUSES
        ):
            run.status = current_status
            break

        if run.budget_blocks is not None and (
            run.processed_blocks + run.failed_blocks + run.skipped_blocks
        ) >= run.budget_blocks:
            run.status = KGExtractionRunStatus.PAUSED_BUDGET.value
            run.pause_reason = "已达到区块预算上限（%s 块），需人工确认后继续。" % run.budget_blocks
            db.commit()
            break
        if run.budget_tokens is not None and run.tokens_used >= run.budget_tokens:
            run.status = KGExtractionRunStatus.PAUSED_BUDGET.value
            run.pause_reason = "已达到 token 预算上限（%s），需人工确认后继续。" % run.budget_tokens
            db.commit()
            break

        if not overwrite_existing and _has_existing_extraction(db, block_id, run):
            db.add(
                KGExtractionItem(
                    run_id=run.id,
                    block_id=block_id,
                    status=KGExtractionItemStatus.SKIPPED.value,
                    tokens=0,
                    error="该区块已有抽取结果（未覆盖）。",
                )
            )
            run.skipped_blocks += 1
            run.cursor_block_id = block_id
            db.commit()
            continue

        try:
            response = extract(db, block_id)
        except Exception as exc:  # noqa: BLE001 - the reason is recorded per block
            transient = _is_transient(exc)
            db.rollback()
            db.add(
                KGExtractionItem(
                    run_id=run.id,
                    block_id=block_id,
                    status=KGExtractionItemStatus.FAILED.value,
                    tokens=0,
                    error=("%s: %s" % (type(exc).__name__, exc))[:500],
                )
            )
            run.failed_blocks += 1
            run.consecutive_failures += 1
            run.cursor_block_id = block_id
            if run.consecutive_failures >= run.max_consecutive_failures:
                run.status = (
                    KGExtractionRunStatus.PAUSED_ERROR.value
                    if transient
                    else KGExtractionRunStatus.PAUSED_FORMAT.value
                )
                run.pause_reason = (
                    "连续 %s 个区块抽取失败（最近一次：%s）。任务已暂停，需人工检查后恢复。"
                    % (run.consecutive_failures, ("%s: %s" % (type(exc).__name__, exc))[:200])
                )
            db.commit()
            if run.status in PAUSED_STATUSES:
                break
            continue

        tokens = 0
        metrics = getattr(response, "metrics", None)
        if metrics is not None:
            tokens = int(getattr(metrics, "total_tokens", 0) or 0)
        db.add(
            KGExtractionItem(
                run_id=run.id,
                block_id=block_id,
                status=KGExtractionItemStatus.SUCCEEDED.value,
                tokens=tokens,
            )
        )
        run.processed_blocks += 1
        run.tokens_used += tokens
        run.token_source = provenance.token_source_for(
            run.source_kind or provenance.SourceKind.UNKNOWN.value, run.tokens_used
        )
        run.consecutive_failures = 0
        run.cursor_block_id = block_id
        # A pause can arrive while the (slow) model call is in flight. The counters are
        # worth persisting, but the operator's status must not be overwritten by this
        # session's stale "running" value on the same commit.
        latest_status = (
            db.query(KGExtractionRun.status)
            .filter(KGExtractionRun.id == run.id)
            .scalar()
        )
        if latest_status is not None and (
            latest_status in PAUSED_STATUSES or latest_status in TERMINAL_STATUSES
        ):
            run.status = latest_status
        db.commit()
        if run.status in PAUSED_STATUSES or run.status in TERMINAL_STATUSES:
            break

    _finish_if_done(db, run)
    return run_status(db, run)


def pause_run(db: Session, run_id: int, reason: Optional[str] = None) -> Dict[str, object]:
    run = db.query(KGExtractionRun).filter(KGExtractionRun.id == run_id).one_or_none()
    if run is None:
        raise RunNotFound("抽取任务 #%s 不存在。" % run_id)
    if run.status not in ACTIVE_STATUSES:
        raise RunStateError("当前状态（%s）不能暂停。" % run.status)
    run.status = KGExtractionRunStatus.PAUSED_MANUAL.value
    run.pause_reason = reason or "已由人工暂停。"
    db.commit()
    return run_status(db, run)


def resume_run(
    db: Session,
    run_id: int,
    *,
    budget_blocks: Optional[int] = None,
    budget_tokens: Optional[int] = None,
) -> Dict[str, object]:
    """Resume a paused run; a budget pause requires a new, explicit ceiling.

    Without a new ceiling the run would pause again on the first block, which looks
    like "nothing happens" to the operator. Supplying one makes the cost decision
    visible instead of silently raising a limit.
    """
    run = db.query(KGExtractionRun).filter(KGExtractionRun.id == run_id).one_or_none()
    if run is None:
        raise RunNotFound("抽取任务 #%s 不存在。" % run_id)
    if run.status not in PAUSED_STATUSES:
        raise RunStateError("当前状态（%s）不需要恢复。" % run.status)
    if run.status == KGExtractionRunStatus.PAUSED_BUDGET.value:
        if budget_blocks is None and budget_tokens is None:
            raise RunStateError(
                "该任务因预算上限暂停（%s），恢复前必须提供新的上限（budget_blocks 或 budget_tokens）。"
                % (run.pause_reason or "预算")
            )
        max_blocks = int(settings.kg_extraction_max_budget_blocks or 0)
        attempted = run.processed_blocks + run.skipped_blocks + run.failed_blocks
        if budget_blocks is not None:
            if isinstance(budget_blocks, bool) or not isinstance(budget_blocks, int) or budget_blocks <= 0:
                raise RunStateError("新的区块上限必须是正整数。")
            if max_blocks and budget_blocks > max_blocks:
                raise RunStateError(
                    "新的区块上限 %d 超过单次上限 %d。" % (budget_blocks, max_blocks)
                )
            if budget_blocks <= attempted:
                raise RunStateError(
                    "新的区块上限（%s）必须大于已处理区块数（%s）。"
                    % (budget_blocks, attempted)
                )
            run.budget_blocks = budget_blocks
        if budget_tokens is not None:
            max_tokens = int(settings.kg_extraction_max_budget_tokens or 0)
            if isinstance(budget_tokens, bool) or not isinstance(budget_tokens, int) or budget_tokens <= 0:
                raise RunStateError("新的 token 上限必须是正整数。")
            if max_tokens and budget_tokens > max_tokens:
                raise RunStateError(
                    "新的 token 上限 %d 超过单次上限 %d。" % (budget_tokens, max_tokens)
                )
            if budget_tokens <= run.tokens_used:
                raise RunStateError(
                    "新的 token 上限（%s）必须大于已用 token 数（%s）。"
                    % (budget_tokens, run.tokens_used)
                )
            run.budget_tokens = budget_tokens
    run.status = KGExtractionRunStatus.RUNNING.value
    run.pause_reason = None
    run.consecutive_failures = 0
    db.commit()
    logger.info("KG extraction run resumed: id=%s (budgets %s/%s)", run.id, run.budget_blocks, run.budget_tokens)
    return run_status(db, run)


def run_status(db: Session, run: KGExtractionRun) -> Dict[str, object]:
    attempted = run.processed_blocks + run.skipped_blocks + run.failed_blocks
    percent = round(100.0 * attempted / run.total_blocks, 1) if run.total_blocks else 0.0
    return {
        "run_id": run.id,
        "scope_type": run.scope_type,
        "scope_id": run.scope_id,
        "group_id": run.group_id,
        "document_id": run.document_id,
        "status": run.status,
        "model": run.model,
        "is_mock_model": is_mock_model(run.model),
        "source_kind": run.source_kind or provenance.classify_model(run.model),
        "source_kind_label": provenance.describe(
            run.source_kind or provenance.classify_model(run.model)
        ),
        "token_source": run.token_source
        or provenance.token_source_for(
            run.source_kind or provenance.classify_model(run.model), run.tokens_used
        ),
        "token_source_label": provenance.describe_tokens(
            run.token_source
            or provenance.token_source_for(
                run.source_kind or provenance.classify_model(run.model), run.tokens_used
            )
        ),
        "prompt_hash": run.prompt_hash,
        "trigger": run.trigger,
        "dispatch_state": run.dispatch_state,
        "dispatch_attempts": run.dispatch_attempts,
        "dispatch_error": run.dispatch_error,
        "total_blocks": run.total_blocks,
        "processed_blocks": run.processed_blocks,
        "skipped_blocks": run.skipped_blocks,
        "failed_blocks": run.failed_blocks,
        "attempted_blocks": attempted,
        "progress_percent": percent,
        "tokens_used": run.tokens_used,
        "budget_blocks": run.budget_blocks,
        "budget_tokens": run.budget_tokens,
        "budget_source": run.budget_source,
        "pause_reason": run.pause_reason,
        "reset_at": run.reset_at.isoformat() if run.reset_at else None,
        "reset_reason": run.reset_reason,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


def is_mock_model(model: Optional[str]) -> bool:
    """True when the run used a local/mock model (its output is not evidence)."""
    if not model:
        return False
    return str(model).strip().lower() in settings.kg_mock_model_name_set


def runs_summary(
    db: Session,
    *,
    group_id: Optional[int] = None,
    document_id: Optional[int] = None,
    limit: int = 10,
) -> Dict[str, object]:
    """Classify the runs of one scope so a stale paused run cannot mask a newer one.

    Roles are distinct on purpose: an *active* run, a *paused* run, the last run that
    finished (completed/partial) and a failed run are different facts about the same
    group, and the UI must be able to show all of them at once.
    """
    runs = list_runs(db, group_id=group_id, document_id=document_id, limit=limit)
    def _first(predicate):
        for run in runs:
            if predicate(run):
                return run
        return None

    active = _first(lambda r: r.status in ACTIVE_STATUSES)
    paused = _first(lambda r: str(r.status).startswith("paused_"))
    last_finished = _first(
        lambda r: r.status
        in (KGExtractionRunStatus.COMPLETED.value, KGExtractionRunStatus.PARTIAL.value)
    )
    failed = _first(lambda r: r.status == KGExtractionRunStatus.FAILED.value)
    return {
        "active_run": run_status(db, active) if active else None,
        "paused_run": run_status(db, paused) if paused else None,
        "last_finished_run": run_status(db, last_finished) if last_finished else None,
        "failed_run": run_status(db, failed) if failed else None,
        "recent_runs": [run_status(db, run) for run in runs],
        #: The run the UI should headline: live work first, otherwise the most recent
        #: attempt (whatever its status) — never an older paused run above newer work.
        "primary_run": run_status(db, active or (runs[0] if runs else None))
        if (active or runs)
        else None,
    }


def latest_run(db: Session, *, group_id: Optional[int] = None, document_id: Optional[int] = None):
    query = db.query(KGExtractionRun)
    if document_id is not None:
        query = query.filter(KGExtractionRun.document_id == document_id)
    elif group_id is not None:
        query = query.filter(KGExtractionRun.group_id == group_id)
    else:
        return None
    return query.order_by(KGExtractionRun.id.desc()).first()


def list_runs(
    db: Session,
    *,
    group_id: Optional[int] = None,
    document_id: Optional[int] = None,
    limit: int = 10,
):
    query = db.query(KGExtractionRun)
    if document_id is not None:
        query = query.filter(KGExtractionRun.document_id == document_id)
    elif group_id is not None:
        query = query.filter(KGExtractionRun.group_id == group_id)
    return query.order_by(KGExtractionRun.id.desc()).limit(limit).all()


def run_until_stopped(
    db: Session,
    run_id: int,
    batch_size: int = 20,
    max_batches: int = 50,
    extractor: Optional[Callable[[Session, int], object]] = None,
) -> Dict[str, object]:
    """Worker loop: keep processing batches until the run stops being active.

    This is what a Celery worker executes; ``max_batches`` bounds one invocation so a
    hung provider cannot pin a worker forever (the next invocation continues from the
    ledger). A run without a block budget is refused here as well: the worker must not
    be the place where an unlimited run becomes possible.
    """
    snapshot: Dict[str, object] = {}
    run = db.query(KGExtractionRun).filter(KGExtractionRun.id == run_id).one_or_none()
    if run is None:
        raise RunNotFound("抽取任务 #%s 不存在。" % run_id)
    if run.budget_blocks is None and run.status in ACTIVE_STATUSES:
        run.status = KGExtractionRunStatus.PAUSED_BUDGET.value
        run.pause_reason = "任务缺少区块预算，已停止以免无上限调用模型；请设置预算后恢复。"
        db.commit()
        return run_status(db, run)
    for _ in range(max_batches):
        snapshot = process_batch(db, run_id, limit=batch_size, extractor=extractor)
        if snapshot.get("status") not in ACTIVE_STATUSES:
            return snapshot
        if snapshot.get("attempted_blocks") == snapshot.get("total_blocks"):
            return snapshot
    return snapshot


def group_extraction_snapshot(db: Session, group_id: int) -> Optional[Dict[str, object]]:
    run = latest_run(db, group_id=group_id)
    return run_status(db, run) if run else None

