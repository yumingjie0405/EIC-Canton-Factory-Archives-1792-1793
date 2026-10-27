"""Reproducible graph snapshots: store what was computed, not just when.

A row with a timestamp is not a snapshot. This service stores, for one group and one
moment:

* the selection (view + filters + algorithm version), so the numbers can be explained;
* the materialised nodes and edges that were actually counted;
* the per-record provenance, so a history view can never look like archival fact when
  it was mock output;
* the excluded counts, so "98 edges" is never reported without "and 12 were filtered".

Three capabilities are distinguished explicitly:

``described``      only the stored description/materialisation is available;
``rebuildable``    the stored node/edge list can be re-rendered without recomputation;
``recomputable``   the stored filters can be re-run against the *current* data, which
                   may legitimately differ from the stored result.

Comparing two snapshots reports structural, review and provenance changes separately,
because "1 relation added" means something very different when the data source changed.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy.orm import Session

from app.core import logger
from app.models.kg.workflow import KGGraphSnapshot
from app.models.research_group import ResearchGroup
from app.services.kg.group_graph import load_scope, scope_overview
from app.services.kg import provenance
from app.services.kg.sna_service import sna_service

CAPABILITY_DESCRIBED = "described"
CAPABILITY_REBUILDABLE = "rebuildable"
CAPABILITY_RECOMPUTABLE = "recomputable"

ALGORITHM_VERSION = "NativeSNAEngine-v1.0"


class SnapshotError(Exception):
    """The snapshot request cannot be fulfilled (HTTP 400/404/409)."""


def _filters_dict(filters: Any) -> Dict[str, Any]:
    if filters is None:
        return {}
    if isinstance(filters, dict):
        return {key: value for key, value in filters.items() if value is not None}
    dump = getattr(filters, "model_dump", None)
    if callable(dump):
        return {key: value for key, value in dump().items() if value is not None}
    return {}


def create_snapshot(
    db: Session,
    *,
    run_id: str,
    group_id: Optional[int] = None,
    name: Optional[str] = None,
    note: Optional[str] = None,
    view: str = "all",
    filters: Any = None,
    created_by: str = "system",
) -> Dict[str, Any]:
    """Materialise the group's current subgraph into a snapshot row."""
    if not run_id or not run_id.strip():
        raise SnapshotError("必须提供快照标识（run_id）。")
    existing = db.query(KGGraphSnapshot).filter(KGGraphSnapshot.run_id == run_id.strip()).one_or_none()
    if existing is not None:
        raise SnapshotError("快照标识 %s 已存在，请换一个名称。" % run_id.strip())

    filter_map = _filters_dict(filters)
    if group_id is None:
        raise SnapshotError("组级快照必须提供 group_id：全局图谱不能冒充某个研究组的快照。")

    scope = load_scope(db, group_id)
    group = db.query(ResearchGroup).filter(ResearchGroup.id == group_id).one_or_none()
    scoped_filters = dict(filter_map)
    scoped_filters["group_id"] = group_id
    scoped_filters.setdefault("view", view)

    from app.schemas.kg_sna import SNAGraphFilterParams

    parsed = SNAGraphFilterParams(**scoped_filters)
    overview = scope_overview(db, scope, parsed)
    summary = sna_service.get_network_summary(db, parsed)

    persons = [
        {
            "id": person["id"] if isinstance(person, dict) else person.id,
            "display_name": (
                person.get("display_name") if isinstance(person, dict) else person.display_name
            ),
            "identity_status": str(
                getattr(
                    person.get("identity_status") if isinstance(person, dict) else person.identity_status,
                    "value",
                    person.get("identity_status") if isinstance(person, dict) else person.identity_status,
                )
            ),
        }
        for person in overview["persons"]
    ]
    relationships = [
        {
            "id": rel.id,
            "source_person_id": rel.source_person_id,
            "target_person_id": rel.target_person_id,
            "relation_type": str(getattr(rel.relation_type, "value", rel.relation_type)),
            "claim_status": str(getattr(rel.claim_status, "value", rel.claim_status)),
            "review_status": str(getattr(rel.review_status, "value", rel.review_status)),
            "evidence_count": overview["evidence_counts"].get(rel.id, 0),
        }
        for rel in overview["relationships"]
    ]

    kinds = _snapshot_source_kinds(db, [rel["id"] for rel in relationships])
    for rel in relationships:
        rel["source_kinds"] = kinds.get(rel["id"], {})

    trust = provenance.trust_payload(
        [kind for mapping in kinds.values() for kind in mapping],
    )
    run_ids = _extraction_run_ids(db, [rel["id"] for rel in relationships])

    snapshot = KGGraphSnapshot(
        run_id=run_id.strip(),
        name=name or run_id.strip(),
        note=note,
        group_id=scope.group_id,
        group_name=group.name if group else scope.group_name,
        view=overview["view"],
        filters=filter_map,
        algorithm_version=ALGORITHM_VERSION,
        parameters={
            "directed": bool(getattr(parsed, "directed", True)),
            "include_isolates": bool(getattr(parsed, "include_isolates", True)),
            "include_provisional": bool(getattr(parsed, "include_provisional", False)),
            "include_unknown_dates": bool(getattr(parsed, "include_unknown_dates", True)),
            "relation_type": getattr(parsed, "relation_type", None),
            "year_from": getattr(parsed, "year_from", None),
            "year_to": getattr(parsed, "year_to", None),
        },
        node_count=len(persons),
        edge_count=len(relationships),
        persons=persons,
        relationships=relationships,
        metrics_summary={
            "node_count": summary.node_count,
            "edge_count": summary.edge_count,
            "aggregated_edge_count": summary.aggregated_edge_count,
            "density": summary.density,
            "average_degree": summary.average_degree,
            "connected_components_count": summary.connected_components_count,
            "isolated_nodes_count": summary.isolated_nodes_count,
            "average_clustering_coefficient": summary.average_clustering_coefficient,
            "evidence_counts": overview.get("evidence_counts"),
        },
        excluded=overview.get("excluded"),
        data_trust=trust,
        extraction_run_ids=run_ids,
        capability=CAPABILITY_RECOMPUTABLE,
        created_by=created_by,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    logger.info(
        "Graph snapshot stored: %s group=%s nodes=%d edges=%d",
        snapshot.run_id,
        snapshot.group_id,
        snapshot.node_count,
        snapshot.edge_count,
    )
    return snapshot_detail(snapshot)


def _snapshot_source_kinds(db: Session, relationship_ids: Sequence[int]) -> Dict[int, Dict[str, int]]:
    if not relationship_ids:
        return {}
    from app.models.kg.relationship import RelationshipEvidence

    rows = (
        db.query(RelationshipEvidence.relationship_id, RelationshipEvidence.source_kind)
        .filter(RelationshipEvidence.relationship_id.in_(list(relationship_ids)))
        .all()
    )
    out: Dict[int, Dict[str, int]] = {}
    for relationship_id, kind in rows:
        bucket = out.setdefault(relationship_id, {})
        key = kind or provenance.SourceKind.UNKNOWN.value
        bucket[key] = bucket.get(key, 0) + 1
    return out


def _extraction_run_ids(db: Session, relationship_ids: Sequence[int]) -> List[int]:
    if not relationship_ids:
        return []
    from app.models.kg.relationship import RelationshipEvidence

    rows = (
        db.query(RelationshipEvidence.extraction_run_id)
        .filter(
            RelationshipEvidence.relationship_id.in_(list(relationship_ids)),
            RelationshipEvidence.extraction_run_id.isnot(None),
        )
        .distinct()
        .all()
    )
    return sorted({row[0] for row in rows})


def snapshot_summary(snapshot: KGGraphSnapshot) -> Dict[str, Any]:
    return {
        "run_id": snapshot.run_id,
        "name": snapshot.name,
        "group_id": snapshot.group_id,
        "group_name": snapshot.group_name,
        "view": snapshot.view,
        "node_count": snapshot.node_count,
        "edge_count": snapshot.edge_count,
        "capability": snapshot.capability,
        "synthetic": bool((snapshot.data_trust or {}).get("synthetic")),
        "created_by": snapshot.created_by,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else None,
    }


def snapshot_detail(snapshot: KGGraphSnapshot) -> Dict[str, Any]:
    return {
        **snapshot_summary(snapshot),
        "note": snapshot.note,
        "filters": snapshot.filters,
        "parameters": snapshot.parameters,
        "algorithm_version": snapshot.algorithm_version,
        "persons": snapshot.persons or [],
        "relationships": snapshot.relationships or [],
        "metrics_summary": snapshot.metrics_summary,
        "excluded": snapshot.excluded,
        "data_trust": snapshot.data_trust,
        "extraction_run_ids": snapshot.extraction_run_ids or [],
        "capabilities": {
            "can_display": True,
            "can_rebuild": snapshot.capability in (CAPABILITY_REBUILDABLE, CAPABILITY_RECOMPUTABLE),
            "can_recompute": snapshot.capability == CAPABILITY_RECOMPUTABLE,
            "explanation": (
                "可直接展示存储时的人物与关系；重新计算会使用*当前*数据，可能与保存时不同。"
                if snapshot.capability == CAPABILITY_RECOMPUTABLE
                else "仅能展示当时保存的结果。"
            ),
        },
    }


def list_snapshots(
    db: Session, *, group_id: Optional[int] = None, limit: int = 20
) -> List[Dict[str, Any]]:
    query = db.query(KGGraphSnapshot)
    if group_id is not None:
        query = query.filter(KGGraphSnapshot.group_id == group_id)
    rows = query.order_by(KGGraphSnapshot.created_at.desc()).limit(max(1, min(limit, 100))).all()
    return [snapshot_summary(row) for row in rows]


def get_snapshot(db: Session, run_id: str) -> Dict[str, Any]:
    snapshot = db.query(KGGraphSnapshot).filter(KGGraphSnapshot.run_id == run_id).one_or_none()
    if snapshot is None:
        raise SnapshotError("快照 %s 不存在。" % run_id)
    return snapshot_detail(snapshot)


def compare_snapshots(db: Session, run_id_a: str, run_id_b: str) -> Dict[str, Any]:
    """Diff two snapshots, keeping structural / review / provenance changes apart."""
    left = db.query(KGGraphSnapshot).filter(KGGraphSnapshot.run_id == run_id_a).one_or_none()
    right = db.query(KGGraphSnapshot).filter(KGGraphSnapshot.run_id == run_id_b).one_or_none()
    if left is None or right is None:
        raise SnapshotError("用于比较的快照不存在。")

    left_persons = {person["id"]: person for person in (left.persons or [])}
    right_persons = {person["id"]: person for person in (right.persons or [])}
    left_rels = {rel["id"]: rel for rel in (left.relationships or [])}
    right_rels = {rel["id"]: rel for rel in (right.relationships or [])}

    added_persons = sorted(set(right_persons) - set(left_persons))
    removed_persons = sorted(set(left_persons) - set(right_persons))
    added_rels = sorted(set(right_rels) - set(left_rels))
    removed_rels = sorted(set(left_rels) - set(right_rels))

    review_changes = []
    evidence_changes = []
    provenance_changes = []
    for relationship_id in sorted(set(left_rels) & set(right_rels)):
        before, after = left_rels[relationship_id], right_rels[relationship_id]
        if (
            before.get("claim_status") != after.get("claim_status")
            or before.get("review_status") != after.get("review_status")
        ):
            review_changes.append(
                {
                    "id": relationship_id,
                    "before": {
                        "claim_status": before.get("claim_status"),
                        "review_status": before.get("review_status"),
                    },
                    "after": {
                        "claim_status": after.get("claim_status"),
                        "review_status": after.get("review_status"),
                    },
                }
            )
        if (before.get("evidence_count") or 0) != (after.get("evidence_count") or 0):
            evidence_changes.append(
                {
                    "id": relationship_id,
                    "before": before.get("evidence_count"),
                    "after": after.get("evidence_count"),
                }
            )
        if (before.get("source_kinds") or {}) != (after.get("source_kinds") or {}):
            provenance_changes.append(
                {
                    "id": relationship_id,
                    "before": before.get("source_kinds") or {},
                    "after": after.get("source_kinds") or {},
                }
            )

    left_trust = (left.data_trust or {}).get("source_kinds") or {}
    right_trust = (right.data_trust or {}).get("source_kinds") or {}

    notes: List[str] = []
    if provenance_changes or left_trust != right_trust:
        notes.append(
            "两次快照的数据来源构成不同：差异的一部分来自抽取来源变化（例如从固定 Mock 换为规则桩），"
            "不能据此描述为新的历史发现。"
        )

    return {
        "left": snapshot_summary(left),
        "right": snapshot_summary(right),
        "parameters_changed": (left.parameters or {}) != (right.parameters or {}),
        "view_changed": left.view != right.view,
        "structure": {
            "persons_added": added_persons,
            "persons_removed": removed_persons,
            "relationships_added": added_rels,
            "relationships_removed": removed_rels,
        },
        "review_changes": review_changes,
        "evidence_changes": evidence_changes,
        "provenance_changes": provenance_changes,
        "provenance_summary": {"before": left_trust, "after": right_trust},
        "notes": notes,
    }
