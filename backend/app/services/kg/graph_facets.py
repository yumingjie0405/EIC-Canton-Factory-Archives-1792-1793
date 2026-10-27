"""Filter facets for the group-scoped person graph.

The rules
---------
1. **Same subgraph.** Counts are computed from :func:`group_graph.scoped_network` — the
   exact function the overview endpoint and the SNA engine use. A facet count can
   therefore never describe a different data set than the rendered graph.
2. **One dimension at a time.** The type counts ignore the current type filter but keep
   the date filter; the year counts ignore the current date filter but keep the type
   filter. That is what makes "if I picked this option, what would I see" truthful.
3. **Relationship dates only.** ``person_mentions.temporal_year`` is a *mention* date
   and must never be presented as the year a relationship was formed. Mention years are
   reported separately, labelled as such.
4. **Empty is valid.** An empty group returns empty facets (never a global fallback), and
   an unknown group still raises ``GroupNotFound``.
5. **Trust is reported, not implied.** Runs whose model is a local/mock model mark the
   data as mock-derived, because "present in the database" is not "verified evidence".
"""
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.kg.extraction_run import KGExtractionRun
from app.models.kg.mention import PersonMention
from app.models.kg.relationship import RelationshipEvidence
from app.models.block import Block
from app.models.page import Page
from app.services.kg import extraction_run_service
from app.services.kg.group_graph import GroupGraphScope, scoped_network

_YEAR_IN_TEXT = re.compile(r"(1[5-9]\d{2})")


def _date_bounds(relationship) -> Optional[tuple]:
    """(first_year, last_year) supported by the relationship's own dates."""
    start = relationship.valid_from
    end = relationship.valid_to
    if start is None and end is None:
        if relationship.time_description:
            years = [int(value) for value in _YEAR_IN_TEXT.findall(relationship.time_description)]
            if years:
                return min(years), max(years)
        return None
    first = (start or end).year
    last = (end or start).year
    return (first, last) if first <= last else (last, first)


def _filters_without(filters, field: str):
    if filters is None:
        return None
    if field == "relation_types":
        return filters.model_copy(update={"relation_types": None})
    if field == "period":
        return filters.model_copy(
            update={"period_start": None, "period_end": None}
        )
    return filters


def data_trust(db: Session, *, group_id: Optional[int], document_id: Optional[int] = None) -> Dict[str, Any]:
    """Report the *record-level* provenance mix of a group's stored graph.

    Run models alone are not enough: after Stage 10 each mention/evidence row carries
    its own ``source_kind``. Counting rows (not runs) makes "how much of this graph is
    stub output" answerable, and keeps ``unknown`` visible for pre-ledger records.
    """
    from app.services.kg import provenance

    query = db.query(KGExtractionRun)
    if group_id is not None:
        query = query.filter(KGExtractionRun.group_id == group_id)
    elif document_id is not None:
        query = query.filter(KGExtractionRun.document_id == document_id)
    else:
        return {
            "mock_derived": False,
            "mock_run_ids": [],
            "models": [],
            "note": "全局视图不属于任何文档组，未做来源判定。",
        }
    runs = query.all()
    models = sorted({run.model for run in runs if run.model})
    mock_runs = [
        run
        for run in runs
        if extraction_run_service.is_mock_model(run.model)
        and (run.processed_blocks or 0) > 0
        and run.reset_at is None
    ]

    block_ids = [
        row[0]
        for row in db.query(Block.id)
        .join(Page, Page.id == Block.page_id)
        .filter(Page.document_id.in_(_scope_document_ids(db, group_id, document_id) or [-1]))
    ] if (group_id is not None or document_id is not None) else []

    kind_counts: Dict[str, int] = {}
    if block_ids:
        for table_rows in (
            db.query(RelationshipEvidence.source_kind).filter(
                RelationshipEvidence.block_id.in_(block_ids)
            ),
            db.query(PersonMention.source_kind).filter(PersonMention.block_id.in_(block_ids)),
        ):
            for (kind,) in table_rows:
                key = kind or provenance.SourceKind.UNKNOWN.value
                kind_counts[key] = kind_counts.get(key, 0) + 1

    synthetic_rows = sum(
        count
        for kind, count in kind_counts.items()
        if provenance.is_synthetic(kind)
    )
    mock_derived = bool(mock_runs) or synthetic_rows > 0 or (
        bool(kind_counts) and synthetic_rows == sum(kind_counts.values())
    )

    if synthetic_rows:
        note = (
            "本组已入库的 %d 条记录中，%d 条来自固定 Mock / 规则桩（%s）。"
            "它们只能用于流程与界面验证，不是档案发现。"
            % (
                sum(kind_counts.values()),
                synthetic_rows,
                "、".join(
                    provenance.describe(kind)
                    for kind in sorted(kind_counts)
                    if provenance.is_synthetic(kind)
                ),
            )
        )
    elif mock_runs:
        note = (
            "该图谱中的记录由本地 Mock 模型生成（run %s），只能用于流程验证，"
            "不是档案发现，也不会进入正式视图。"
            % ", ".join("#%s" % run.id for run in mock_runs)
        )
    elif runs:
        note = "未检测到 Mock 运行；来源仍需人工审核后才可作为正式证据。"
    else:
        note = "该范围尚无抽取运行记录。"

    return {
        "mock_derived": mock_derived,
        "mock_run_ids": [run.id for run in mock_runs],
        "models": models,
        "source_kinds": kind_counts,
        "synthetic_record_count": synthetic_rows,
        "note": note,
    }


def _scope_document_ids(
    db: Session, group_id: Optional[int], document_id: Optional[int]
) -> List[int]:
    if document_id is not None:
        return [document_id]
    if group_id is None:
        return []
    from app.models.research_group import ResearchGroupDocument

    return [
        row[0]
        for row in db.query(ResearchGroupDocument.document_id).filter(
            ResearchGroupDocument.group_id == group_id
        )
    ]


def group_graph_facets(db: Session, scope: GroupGraphScope, filters) -> Dict[str, Any]:
    """Filter metadata for one group under the current view/filters."""
    view = filters.resolved_view() if filters is not None else "all"

    # ---- relationship types (date filter applied, type filter cleared)
    _, typed_relationships = scoped_network(db, scope, _filters_without(filters, "relation_types"))
    type_counts: Dict[str, int] = {}
    for relationship in typed_relationships:
        value = str(getattr(relationship.relation_type, "value", relationship.relation_type))
        type_counts[value] = type_counts.get(value, 0) + 1
    relation_types = [
        {"value": value, "count": count}
        for value, count in sorted(type_counts.items(), key=lambda item: (-item[1], item[0]))
    ]

    # ---- relationship years (type filter applied, date filter cleared)
    _, dated_relationships = scoped_network(db, scope, _filters_without(filters, "period"))
    year_counts: Dict[int, int] = {}
    undated = 0
    for relationship in dated_relationships:
        bounds = _date_bounds(relationship)
        if bounds is None:
            undated += 1
            continue
        first, last = bounds
        for year in range(first, last + 1):
            year_counts[year] = year_counts.get(year, 0) + 1
    dated_years = [
        {"year": year, "count": count} for year, count in sorted(year_counts.items())
    ]

    total = len(dated_relationships)
    time_filter_available = bool(dated_years)
    if not total:
        time_filter_reason = "当前视图下没有关系，年份筛选不可用。"
    elif not time_filter_available:
        time_filter_reason = "本组关系暂无可用的日期信息，时间筛选不可用。"
    else:
        time_filter_reason = None

    include_unknown = bool(getattr(filters, "include_unknown_dates", True)) if filters else True

    # ---- mention years (a *mention* date, never a relationship date)
    mention_rows = (
        db.query(PersonMention.temporal_year, func.count(PersonMention.id))
        .filter(
            PersonMention.block_id.in_(list(scope.block_ids) or [-1]),
            PersonMention.temporal_year.isnot(None),
        )
        .group_by(PersonMention.temporal_year)
        .order_by(PersonMention.temporal_year)
        .all()
    )
    mention_years = [
        {"year": int(year), "count": int(count)} for year, count in mention_rows if year
    ]

    person_ids, _ = scoped_network(db, scope, filters)
    return {
        "group_id": scope.group_id,
        "group_name": scope.group_name,
        "view": view,
        "relation_types": relation_types,
        "dated_years": dated_years,
        "year_range": (
            {"min": dated_years[0]["year"], "max": dated_years[-1]["year"]}
            if dated_years
            else None
        ),
        "undated_relationship_count": undated,
        "total_relationship_count": total,
        "time_filter_available": time_filter_available,
        "time_filter_reason": time_filter_reason,
        "include_unknown_dates": include_unknown,
        "mention_years": mention_years,
        "mention_year_note": "mention_years 是人物提及的年份，不等于关系成立年份。",
        "scope_person_count": len(person_ids),
        "data_trust": data_trust(db, group_id=scope.group_id),
    }
