"""Persistence service for research records (Graph RAG answers and Agent runs).

Design notes
------------
* The service stores the result object the research pipeline already produced;
  it never re-derives the answer, re-runs retrieval or calls the model.
* A failed write is reported to the caller (:class:`HistoryLink.saved` stays
  ``False``); it is never downgraded to a silent success.
* Error messages are scrubbed before they reach the database or the API.
"""
from typing import Any, Optional, Sequence, Tuple

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core import logger
from app.models.research import ResearchMode, ResearchSession, ResearchStatus
from app.repositories.research_repository import ResearchRepository
from app.schemas.research import HistoryLink
from app.services.rag.answer_integrity import scrub_secrets

MODE_RAG = ResearchMode.RAG.value
MODE_AGENT = ResearchMode.AGENT.value

_GENERATION_REFUSED = "refused_no_evidence"

_MAX_QUESTION_CHARS = 2000
_MAX_ERROR_CHARS = 300


def _enum_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def derive_status(response: Any) -> ResearchStatus:
    """Map a research response onto a stored status without ever upgrading it.

    ``refused_no_evidence`` / ``evidence_sufficient=False`` wins over
    ``degraded`` because it describes why there is no answer at all.
    """
    generation = getattr(response, "generation", None)
    generation_mode = getattr(generation, "mode", None)
    if not bool(getattr(response, "evidence_sufficient", False)) or generation_mode == _GENERATION_REFUSED:
        return ResearchStatus.INSUFFICIENT_EVIDENCE
    if bool(getattr(generation, "degraded", False)):
        return ResearchStatus.DEGRADED
    return ResearchStatus.COMPLETED


def _extract_answer(response: Any) -> str:
    answer = getattr(response, "answer", None)
    if answer is None:
        answer = getattr(response, "final_answer", None)
    return str(answer or "")


def _extract_elapsed_ms(response: Any) -> Optional[float]:
    direct = getattr(response, "execution_time_ms", None)
    if direct is not None:
        return float(direct)
    budget = getattr(response, "budget_used", None)
    elapsed = getattr(budget, "elapsed_ms", None)
    return float(elapsed) if elapsed is not None else None


def _dump_list(items: Sequence[Any]) -> list:
    dumped = []
    for item in items or ():
        if hasattr(item, "model_dump"):
            dumped.append(item.model_dump())
        elif isinstance(item, dict):
            dumped.append(dict(item))
    return dumped


def build_record(
    mode: str,
    response: Any,
    question: Optional[str] = None,
    group_id: Optional[int] = None,
    group_name: Optional[str] = None,
) -> ResearchSession:
    """Build (but do not persist) a :class:`ResearchSession` from a response object."""
    generation = getattr(response, "generation", None)
    citation = getattr(generation, "citation_check", None)
    resolved_question = question or getattr(response, "question", "") or ""

    return ResearchSession(
        mode=ResearchMode(mode),
        question=resolved_question[:_MAX_QUESTION_CHARS],
        answer=_extract_answer(response),
        status=derive_status(response),
        evidence_sufficient=bool(getattr(response, "evidence_sufficient", False)),
        generation_mode=getattr(generation, "mode", None),
        model_name=getattr(generation, "model_name", None),
        failure_reason=getattr(generation, "failure_reason", None),
        failure_message=getattr(generation, "failure_message", None),
        failure_detail=getattr(generation, "failure_detail", None),
        citation_valid=getattr(citation, "valid", None) if citation is not None else None,
        citation_invalid_ids=list(getattr(citation, "invalid_ids", []) or []) if citation is not None else None,
        citation_available_count=(
            int(getattr(citation, "available_count", 0) or 0) if citation is not None else None
        ),
        sources=_dump_list(getattr(response, "sources", [])),
        entities=_dump_list(getattr(response, "entities", [])),
        relationships=_dump_list(getattr(response, "relationships", [])),
        intent=(response.intent.model_dump() if getattr(response, "intent", None) is not None else None),
        warnings=list(getattr(response, "warnings", []) or []),
        elapsed_ms=_extract_elapsed_ms(response),
        group_id=group_id if group_id is not None else getattr(response, "group_id", None),
        group_name=group_name if group_name is not None else getattr(response, "group_name", None),
    )


class ResearchHistoryService:
    """Save, list and fetch research records."""

    def save_response(
        self,
        db: Session,
        mode: str,
        response: Any,
        question: Optional[str] = None,
        group_id: Optional[int] = None,
        group_name: Optional[str] = None,
    ) -> HistoryLink:
        """Persist a research response, reporting failure instead of hiding it."""
        try:
            record = build_record(
                mode, response, question=question, group_id=group_id, group_name=group_name
            )
            saved = ResearchRepository(db).add(record)
            logger.info(
                "Saved research record id=%s mode=%s status=%s sources=%d",
                saved.id, _enum_value(saved.mode), _enum_value(saved.status), len(saved.sources or []),
            )
            return HistoryLink(saved=True, record_id=saved.id, mode=mode)
        except SQLAlchemyError as exc:
            db.rollback()
            detail = scrub_secrets("%s: %s" % (type(exc).__name__, exc))[:_MAX_ERROR_CHARS]
            logger.error("Failed to persist research record: %s", detail)
            return HistoryLink(saved=False, mode=mode, error="研究记录未能写入数据库（%s）" % detail)
        except Exception as exc:  # noqa: BLE001 - persistence must never mask the answer
            detail = scrub_secrets("%s: %s" % (type(exc).__name__, exc))[:_MAX_ERROR_CHARS]
            logger.error("Unexpected failure persisting research record: %s", detail)
            return HistoryLink(saved=False, mode=mode, error="研究记录未能写入数据库（%s）" % detail)

    def save_failure(
        self,
        db: Session,
        mode: str,
        question: str,
        exc: BaseException,
        group_id: Optional[int] = None,
        group_name: Optional[str] = None,
    ) -> HistoryLink:
        """Record a run that raised, so failures are visible in history too."""
        detail = scrub_secrets("%s: %s" % (type(exc).__name__, exc))[:_MAX_ERROR_CHARS]
        record = ResearchSession(
            mode=ResearchMode(mode),
            question=(question or "")[:_MAX_QUESTION_CHARS],
            answer="",
            status=ResearchStatus.FAILED,
            evidence_sufficient=False,
            generation_mode=None,
            failure_reason="research_pipeline_failed",
            failure_message="研究流程执行失败，未生成任何结论。",
            failure_detail=detail,
            sources=[],
            entities=[],
            relationships=[],
            warnings=[],
            group_id=group_id,
            group_name=group_name,
        )
        try:
            saved = ResearchRepository(db).add(record)
            return HistoryLink(saved=True, record_id=saved.id, mode=mode)
        except Exception as write_exc:  # noqa: BLE001 - never mask the original failure
            try:
                db.rollback()
            except Exception:  # noqa: BLE001
                pass
            logger.error(
                "Failed to persist failed-research record: %s",
                scrub_secrets("%s: %s" % (type(write_exc).__name__, write_exc))[:_MAX_ERROR_CHARS],
            )
            return HistoryLink(saved=False, mode=mode, error="研究失败记录未能写入数据库")

    def list_records(
        self,
        db: Session,
        page: int = 1,
        page_size: int = 20,
        mode: Optional[str] = None,
        group_id: Optional[int] = None,
    ) -> Tuple[list, int]:
        """Return ``(records, total)`` for one page, newest first."""
        repo = ResearchRepository(db)
        mode_value = ResearchMode(mode) if mode else None
        offset = (page - 1) * page_size
        return (
            repo.list(offset=offset, limit=page_size, mode=mode_value, group_id=group_id),
            repo.count(mode=mode_value, group_id=group_id),
        )

    def get_record(self, db: Session, record_id: int) -> Optional[ResearchSession]:
        return ResearchRepository(db).get_by_id(record_id)


research_history_service = ResearchHistoryService()
