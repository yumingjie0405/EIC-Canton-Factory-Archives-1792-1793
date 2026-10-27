"""Social Network Analysis (SNA) service for historical canton factory entities.

Responsible for:
- Querying validated Persons and Relationships with spatiotemporal and epistemological filtering
- Constructing topologies via SNAEngine
- Calculating network-level and node-level metrics
- Grounding neighbor queries with archival RelationshipEvidence
- Persisting and retrieving SNA metric snapshots in `sna_graph_metrics`
"""
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import uuid

from sqlalchemy.orm import Session

from app.core import logger
from app.models.kg.enums import ClaimStatus, IdentityStatus, RelationType
from app.models.kg.metrics import SNAGraphMetric
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship, RelationshipEvidence
from app.schemas.kg_sna import (
    SNAGraphFilterParams,
    SNANetworkSummaryResponse,
    SNAPersonMetricsResponse,
    SNAPersonNeighborEvidence,
    SNAPersonNeighborItem,
    SNAPersonNeighborsResponse,
    SNASnapshotCreateRequest,
    SNASnapshotResponse,
)
from app.services.kg import provenance
from app.services.kg.group_graph import (
    load_scope,
    relationship_claim_filter,
    scoped_network,
)
from app.services.kg.sna_engine import NetworkMetricsResult, SNAEngine


class SNAService:
    """Service layer orchestrating historical social network analysis."""

    def __init__(self, algorithm_version: str = "NativeSNAEngine-v1.0"):
        self.algorithm_version = algorithm_version

    def build_network_graph(
        self,
        db: Session,
        filters: Optional[SNAGraphFilterParams] = None,
    ) -> Tuple[SNAEngine, Dict[int, Person], List[Relationship], NetworkMetricsResult]:
        """Construct graph and execute metrics computation according to explicit filters.

        When ``filters.group_id`` is set the network is built from **that group's own
        evidence**: persons need an in-group mention or an in-group-evidenced
        relationship, and a relationship must have at least one evidence block inside
        the group. An empty scope yields an empty graph — never the global one.
        """
        if filters is None:
            filters = SNAGraphFilterParams()

        scope = None
        if filters.group_id is not None:
            scope = load_scope(db, filters.group_id)

        if scope is not None:
            # Single authoritative subgraph definition (shared with the read-only
            # overview endpoint): identity rules, evidence scope and view applied.
            scoped_ids, raw_relationships = scoped_network(db, scope, filters)
            person_query = db.query(Person).filter(Person.id.in_(scoped_ids))
            valid_persons = person_query.all()
            persons_map: Dict[int, Person] = {p.id: p for p in valid_persons}
            valid_person_ids = set(persons_map.keys())
        else:
            # 1. Filter valid Person nodes
            person_query = db.query(Person).filter(
                Person.identity_status != IdentityStatus.REJECTED
            )
            if not filters.include_provisional:
                person_query = person_query.filter(
                    Person.identity_status == IdentityStatus.ACCEPTED
                )

            valid_persons = person_query.all()
            persons_map = {p.id: p for p in valid_persons}
            valid_person_ids = set(persons_map.keys())

            # 2. Query relationships connecting valid persons
            rel_query = db.query(Relationship).filter(
                Relationship.source_person_id.in_(valid_person_ids),
                Relationship.target_person_id.in_(valid_person_ids),
                Relationship.claim_status != ClaimStatus.REJECTED,
            )
            if filters.view is not None:
                rel_query = rel_query.filter(relationship_claim_filter(filters.view))
            if filters.relation_types:
                rel_query = rel_query.filter(
                    Relationship.relation_type.in_(filters.relation_types)
                )
            if filters.min_confidence is not None:
                rel_query = rel_query.filter(
                    Relationship.confidence >= filters.min_confidence
                )
            raw_relationships = rel_query.all()

        # 3. Filter relationships by spatiotemporal rules and exclude self-loops
        active_relationships: List[Relationship] = []
        self_loops_count = 0
        for rel in raw_relationships:
            # Exclude self-loops from SNA network topology
            if rel.source_person_id == rel.target_person_id:
                self_loops_count += 1
                continue

            # Check date availability
            if rel.valid_from is None and rel.valid_to is None:
                if not filters.include_unknown_dates:
                    continue
            else:
                # If filter specifies start date, relationship cannot end strictly before start date
                if filters.period_start is not None and rel.valid_to is not None:
                    if rel.valid_to < filters.period_start:
                        continue
                # If filter specifies end date, relationship cannot begin strictly after end date
                if filters.period_end is not None and rel.valid_from is not None:
                    if rel.valid_from > filters.period_end:
                        continue

            active_relationships.append(rel)

        # 4. Construct graph inside SNAEngine
        engine = SNAEngine(directed=filters.directed)

        # Pre-add all valid persons if isolates are included
        if filters.include_isolates:
            for pid in valid_person_ids:
                engine.add_node(pid)

        # Add edges (ignore self-loops in centrality topology calculation)
        for rel in active_relationships:
            engine.add_edge(
                u=rel.source_person_id,
                v=rel.target_person_id,
                weight=rel.weight if rel.weight and rel.weight > 0 else 1.0,
                metadata={"relation_id": rel.id, "relation_type": rel.relation_type.value if hasattr(rel.relation_type, "value") else str(rel.relation_type)},
                ignore_self_loop=True,
            )

        # If isolates are not included, prune 0-degree nodes
        if not filters.include_isolates:
            degrees = engine.get_degrees()
            for node, d_info in list(degrees.items()):
                if d_info["degree"] == 0:
                    engine.nodes.remove(node)
                    engine.adj.pop(node, None)
                    engine.pred.pop(node, None)

        # 5. Compute network summary and node metrics
        metrics_result = engine.compute_network_summary()
        return engine, persons_map, active_relationships, metrics_result

    def get_network_summary(
        self,
        db: Session,
        filters: Optional[SNAGraphFilterParams] = None,
    ) -> SNANetworkSummaryResponse:
        """Calculate and return network macro topological summary."""
        if filters is None:
            filters = SNAGraphFilterParams()

        engine, persons_map, active_relationships, metrics_result = self.build_network_graph(db, filters)

        scope = load_scope(db, filters.group_id) if filters.group_id is not None else None
        filter_dict = {
            "relation_types": [rt.value if hasattr(rt, "value") else str(rt) for rt in filters.relation_types] if filters.relation_types else None,
            "period_start": filters.period_start.isoformat() if filters.period_start else None,
            "period_end": filters.period_end.isoformat() if filters.period_end else None,
            "include_unknown_dates": filters.include_unknown_dates,
            "min_confidence": filters.min_confidence,
            "include_provisional": filters.include_provisional,
            "include_isolates": filters.include_isolates,
            "directed": filters.directed,
            "group_id": filters.group_id,
            "view": filters.resolved_view(),
        }

        return SNANetworkSummaryResponse(
            node_count=metrics_result.node_count,
            edge_count=len(active_relationships),
            aggregated_edge_count=metrics_result.edge_count,
            density=metrics_result.density,
            average_degree=metrics_result.average_degree,
            average_in_degree=metrics_result.average_in_degree if filters.directed else None,
            average_out_degree=metrics_result.average_out_degree if filters.directed else None,
            connected_components_count=metrics_result.connected_components_count,
            component_sizes=metrics_result.component_sizes,
            average_clustering_coefficient=metrics_result.average_clustering_coefficient,
            isolated_nodes_count=metrics_result.isolated_nodes_count,
            filter_parameters=filter_dict,
            algorithm_version=self.algorithm_version,
            group_id=filters.group_id,
            group_name=scope.group_name if scope else None,
            view=filters.resolved_view(),
            scope_document_count=scope.document_count if scope else None,
            scope_block_count=scope.block_count if scope else None,
            **self._edge_provenance(db, active_relationships, scope),
        )

    @staticmethod
    def _edge_provenance(
        db: Session,
        relationships: Sequence[Relationship],
        scope=None,
    ) -> Dict[str, object]:
        """Provenance composition of the measured edges + an explicit metrics rule.

        Centrality is computed over every edge in scope, including mock-derived ones, so
        the payload must say so: a hub produced by a constant mock response is a property
        of the extractor, not of the historical network. Reporting the composition keeps
        the numbers comparable (same subgraph as the rendered graph) while preventing a
        "central figure" claim from a synthetic network.

        Evidence is counted only inside the group's own blocks: a claim that also has
        foreign evidence must not inherit its provenance across the boundary.
        """
        relationship_ids = [relationship.id for relationship in relationships]
        kinds_by_relationship: Dict[int, List[str]] = {}

        if relationship_ids:
            query = db.query(
                RelationshipEvidence.relationship_id, RelationshipEvidence.source_kind
            ).filter(RelationshipEvidence.relationship_id.in_(relationship_ids))
            if scope is not None:
                if not scope.block_ids:
                    query = None
                else:
                    query = query.filter(RelationshipEvidence.block_id.in_(scope.block_ids))
            rows = query.all() if query is not None else []
            for relationship_id, kind in rows:
                kinds_by_relationship.setdefault(int(relationship_id), []).append(kind)

        composition: Dict[str, int] = {}
        verified = synthetic = unknown = 0
        for relationship in relationships:
            combined = provenance.combine(kinds_by_relationship.get(relationship.id, []))
            composition[combined] = composition.get(combined, 0) + 1
            if provenance.is_synthetic(combined) or not kinds_by_relationship.get(relationship.id):
                synthetic += 1
            elif provenance.is_unverified(combined):
                unknown += 1
            else:
                verified += 1

        if synthetic or unknown:
            basis = (
                "边权重来自当前范围内的全部关系证据（包含 Mock/规则桩/来源不明记录），"
                "因此中心性等指标反映的是抽取流程本身，不能作为历史影响力的结论。"
            )
        else:
            basis = "边权重来自当前范围内的全部关系证据（均为真实模型或人工种子来源），仍受档案存佚限制。"

        return {
            "edge_source_composition": composition,
            "verified_edge_count": verified,
            "synthetic_edge_count": synthetic,
            "unknown_edge_count": unknown,
            "metrics_basis": basis,
        }

    def get_person_metrics(
        self,
        db: Session,
        person_id: int,
        filters: Optional[SNAGraphFilterParams] = None,
    ) -> SNAPersonMetricsResponse:
        """Calculate and return individual node-level metrics and rankings."""
        if filters is None:
            filters = SNAGraphFilterParams()

        person = db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person with id {person_id} not found in database")

        engine, persons_map, active_relationships, metrics_result = self.build_network_graph(db, filters)

        # Check if person is included in the analyzed network
        if person_id not in engine.nodes:
            exclusion_reason = "Person exists in database but is excluded from the current network scope "
            if person.identity_status == IdentityStatus.REJECTED:
                exclusion_reason += "(identity_status is REJECTED)."
            elif person.identity_status == IdentityStatus.PROVISIONAL and not filters.include_provisional:
                exclusion_reason += "(identity_status is PROVISIONAL while include_provisional=False)."
            elif not filters.include_isolates:
                exclusion_reason += "(person has degree 0 and include_isolates=False)."
            else:
                exclusion_reason += "under applied filter parameters."

            return SNAPersonMetricsResponse(
                person_id=person.id,
                display_name=person.display_name,
                canonical_name_en=person.canonical_name_en,
                canonical_name_zh=person.canonical_name_zh,
                primary_role=person.primary_role,
                organization=person.organization,
                in_network=False,
                note=exclusion_reason,
            )

        node_m = metrics_result.node_metrics.get(person_id, {})

        # Compute rankings with stable secondary sorting (person_id ascending)
        all_nodes = list(engine.nodes)

        # Rank degree
        sorted_by_deg = sorted(
            all_nodes,
            key=lambda nid: (metrics_result.node_metrics[nid]["degree"], -nid),
            reverse=True,
        )
        rank_deg = sorted_by_deg.index(person_id) + 1

        # Rank betweenness
        sorted_by_btw = sorted(
            all_nodes,
            key=lambda nid: (metrics_result.node_metrics[nid]["betweenness_centrality"], -nid),
            reverse=True,
        )
        rank_btw = sorted_by_btw.index(person_id) + 1

        # Rank closeness
        sorted_by_cls = sorted(
            all_nodes,
            key=lambda nid: (metrics_result.node_metrics[nid]["closeness_centrality"], -nid),
            reverse=True,
        )
        rank_cls = sorted_by_cls.index(person_id) + 1

        # Rank pagerank
        sorted_by_pr = sorted(
            all_nodes,
            key=lambda nid: (metrics_result.node_metrics[nid]["pagerank"], -nid),
            reverse=True,
        )
        rank_pr = sorted_by_pr.index(person_id) + 1

        return SNAPersonMetricsResponse(
            person_id=person.id,
            display_name=person.display_name,
            canonical_name_en=person.canonical_name_en,
            canonical_name_zh=person.canonical_name_zh,
            primary_role=person.primary_role,
            organization=person.organization,
            in_network=True,
            degree=node_m.get("degree", 0),
            in_degree=node_m.get("in_degree", 0),
            out_degree=node_m.get("out_degree", 0),
            degree_centrality=node_m.get("degree_centrality", 0.0),
            betweenness_centrality=node_m.get("betweenness_centrality", 0.0),
            closeness_centrality=node_m.get("closeness_centrality", 0.0),
            pagerank=node_m.get("pagerank", 0.0),
            clustering_coefficient=node_m.get("clustering_coefficient", 0.0),
            community_id=node_m.get("community_id"),
            rank_degree=rank_deg,
            rank_betweenness=rank_btw,
            rank_closeness=rank_cls,
            rank_pagerank=rank_pr,
        )

    def get_person_neighbors(
        self,
        db: Session,
        person_id: int,
        filters: Optional[SNAGraphFilterParams] = None,
    ) -> SNAPersonNeighborsResponse:
        """Find topological neighbors and assemble connecting relationships with archival evidence."""
        if filters is None:
            filters = SNAGraphFilterParams()

        person = db.query(Person).filter(Person.id == person_id).first()
        if not person:
            raise ValueError(f"Person with id {person_id} not found in database")

        engine, persons_map, active_relationships, metrics_result = self.build_network_graph(db, filters)

        if person_id not in engine.nodes:
            return SNAPersonNeighborsResponse(
                person_id=person.id,
                display_name=person.display_name,
                neighbor_count=0,
                neighbors=[],
            )

        # Collect relationships connected to person_id
        # target -> relationships where person is source
        out_rels: Dict[int, List[Relationship]] = {}
        # source -> relationships where person is target
        in_rels: Dict[int, List[Relationship]] = {}

        for rel in active_relationships:
            if rel.source_person_id == person_id and rel.target_person_id != person_id:
                out_rels.setdefault(rel.target_person_id, []).append(rel)
            elif rel.target_person_id == person_id and rel.source_person_id != person_id:
                in_rels.setdefault(rel.source_person_id, []).append(rel)

        neighbor_ids = set(out_rels.keys()) | set(in_rels.keys())
        neighbor_items: List[SNAPersonNeighborItem] = []

        for nid in sorted(neighbor_ids):
            nbr_person = persons_map.get(nid)
            if not nbr_person:
                nbr_person = db.query(Person).filter(Person.id == nid).first()
            if not nbr_person:
                continue

            has_out = nid in out_rels
            has_in = nid in in_rels
            if has_out and has_in:
                direction = "mutual"
            elif has_out:
                direction = "out"
            else:
                direction = "in"

            all_connected_rels = out_rels.get(nid, []) + in_rels.get(nid, [])
            types_set = {
                r.relation_type.value if hasattr(r.relation_type, "value") else str(r.relation_type)
                for r in all_connected_rels
            }

            # Gather evidences for all connected relationships
            rel_ids = [r.id for r in all_connected_rels]
            evidences_db = (
                db.query(RelationshipEvidence)
                .filter(RelationshipEvidence.relationship_id.in_(rel_ids))
                .all()
            )

            evidence_items: List[SNAPersonNeighborEvidence] = []
            evidence_block_ids: Set[int] = set()
            for ev in evidences_db:
                evidence_block_ids.add(ev.block_id)
                evidence_items.append(
                    SNAPersonNeighborEvidence(
                        id=ev.id,
                        relationship_id=ev.relationship_id,
                        block_id=ev.block_id,
                        quote_text=ev.quote_text[:200] if ev.quote_text else None,  # Compact excerpt
                        evidence_role=ev.evidence_role.value if hasattr(ev.evidence_role, "value") else str(ev.evidence_role),
                    )
                )

            neighbor_items.append(
                SNAPersonNeighborItem(
                    person_id=nbr_person.id,
                    display_name=nbr_person.display_name,
                    canonical_name_en=nbr_person.canonical_name_en,
                    canonical_name_zh=nbr_person.canonical_name_zh,
                    primary_role=nbr_person.primary_role,
                    organization=nbr_person.organization,
                    direction=direction,
                    relation_count=len(all_connected_rels),
                    relation_types=sorted(list(types_set)),
                    evidence_count=len(evidence_items),
                    evidence_block_ids=sorted(list(evidence_block_ids)),
                    evidences=evidence_items,
                )
            )

        return SNAPersonNeighborsResponse(
            person_id=person.id,
            display_name=person.display_name,
            neighbor_count=len(neighbor_items),
            neighbors=neighbor_items,
        )

    def create_and_persist_snapshot(
        self,
        db: Session,
        request: SNASnapshotCreateRequest,
    ) -> SNASnapshotResponse:
        """Execute full SNA analysis and persist snapshot into `sna_graph_metrics` table."""
        run_id = request.run_id or f"sna_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        filters = request.filters or SNAGraphFilterParams()

        engine, persons_map, active_relationships, metrics_result = self.build_network_graph(db, filters)
        summary = self.get_network_summary(db, filters)

        snapshot_now = datetime.utcnow()
        persisted_count = 0

        parameters_payload = {
            "filters": summary.filter_parameters,
            "summary": {
                "node_count": summary.node_count,
                "edge_count": summary.edge_count,
                "density": summary.density,
                "average_degree": summary.average_degree,
                "connected_components_count": summary.connected_components_count,
            },
        }

        # Persist metrics for each included node
        for node_id, n_metrics in metrics_result.node_metrics.items():
            # Check for existing record under this run_id and person_id
            existing = (
                db.query(SNAGraphMetric)
                .filter(
                    SNAGraphMetric.run_id == run_id,
                    SNAGraphMetric.person_id == node_id,
                )
                .first()
            )

            if existing:
                existing.degree_centrality = n_metrics["degree_centrality"]
                existing.betweenness_centrality = n_metrics["betweenness_centrality"]
                existing.closeness_centrality = n_metrics["closeness_centrality"]
                existing.pagerank = n_metrics["pagerank"]
                existing.community_id = n_metrics.get("community_id")
                existing.algorithm_version = self.algorithm_version
                existing.parameters = parameters_payload
                existing.snapshot_at = snapshot_now
                existing.period_start = filters.period_start
                existing.period_end = filters.period_end
                existing.graph_scope = request.graph_scope
            else:
                metric_row = SNAGraphMetric(
                    person_id=node_id,
                    run_id=run_id,
                    snapshot_at=snapshot_now,
                    period_start=filters.period_start,
                    period_end=filters.period_end,
                    graph_scope=request.graph_scope,
                    degree_centrality=n_metrics["degree_centrality"],
                    betweenness_centrality=n_metrics["betweenness_centrality"],
                    closeness_centrality=n_metrics["closeness_centrality"],
                    pagerank=n_metrics["pagerank"],
                    community_id=n_metrics.get("community_id"),
                    algorithm_version=self.algorithm_version,
                    parameters=parameters_payload,
                )
                db.add(metric_row)

            persisted_count += 1

        db.commit()
        logger.info(
            "Persisted SNA snapshot '%s' with %d person metrics in scope '%s'",
            run_id,
            persisted_count,
            request.graph_scope,
        )

        return SNASnapshotResponse(
            run_id=run_id,
            snapshot_at=snapshot_now,
            graph_scope=request.graph_scope,
            node_count=metrics_result.node_count,
            metrics_persisted_count=persisted_count,
            summary=summary,
        )

    def list_snapshots(self, db: Session, limit: int = 20) -> List[Dict[str, Any]]:
        """Query unique snapshot runs recorded in the database."""
        records = (
            db.query(
                SNAGraphMetric.run_id,
                SNAGraphMetric.graph_scope,
                SNAGraphMetric.snapshot_at,
                SNAGraphMetric.algorithm_version,
                SNAGraphMetric.period_start,
                SNAGraphMetric.period_end,
            )
            .distinct()
            .order_by(SNAGraphMetric.snapshot_at.desc())
            .limit(limit)
            .all()
        )

        result = []
        for r in records:
            count = db.query(SNAGraphMetric).filter(SNAGraphMetric.run_id == r.run_id).count()
            result.append({
                "run_id": r.run_id,
                "graph_scope": r.graph_scope,
                "snapshot_at": r.snapshot_at,
                "algorithm_version": r.algorithm_version,
                "period_start": r.period_start,
                "period_end": r.period_end,
                "node_count": count,
            })
        return result


# Singleton instance for application injection
sna_service = SNAService()
