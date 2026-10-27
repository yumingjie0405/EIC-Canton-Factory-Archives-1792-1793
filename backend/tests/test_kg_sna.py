"""Comprehensive tests for Stage 4 Social Network Analysis (SNA) engine, service, and APIs."""
from datetime import date, datetime
import unittest

from fastapi.testclient import TestClient

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.database import get_db
from app.test_support.db_fixtures import (
    drop_test_schema,
    make_session_factory,
    make_test_engine,
)
from app.main import app
from app.models.block import Block
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.page import Page
from app.models.kg.enums import (
    ClaimStatus,
    Directionality,
    EvidenceRole,
    IdentityStatus,
    RelationType,
    ReviewStatusKG,
)
from app.models.kg.metrics import SNAGraphMetric
from app.models.kg.person import Person
from app.models.kg.relationship import Relationship, RelationshipEvidence
from app.schemas.kg_sna import SNAGraphFilterParams, SNASnapshotCreateRequest
from app.services.kg.sna_engine import SNAEngine
from app.services.kg.sna_service import SNAService


class TestSNAEngineTopologies(unittest.TestCase):
    """Mathematical verification of SNA algorithms on canonical graph topologies."""

    def test_empty_graph(self):
        """Empty graph (0 nodes, 0 edges) boundary handling."""
        g = SNAEngine(directed=True)
        summary = g.compute_network_summary()
        self.assertEqual(summary.node_count, 0)
        self.assertEqual(summary.edge_count, 0)
        self.assertEqual(summary.density, 0.0)
        self.assertEqual(summary.average_degree, 0.0)
        self.assertEqual(summary.connected_components_count, 0)
        self.assertEqual(summary.isolated_nodes_count, 0)

    def test_single_node_graph(self):
        """Single isolated node (1 node, 0 edges) boundary handling."""
        g = SNAEngine(directed=True)
        g.add_node("SingleNode")
        summary = g.compute_network_summary()
        self.assertEqual(summary.node_count, 1)
        self.assertEqual(summary.edge_count, 0)
        self.assertEqual(summary.density, 0.0)
        self.assertEqual(summary.isolated_nodes_count, 1)
        m = summary.node_metrics["SingleNode"]
        self.assertEqual(m["degree"], 0)
        self.assertEqual(m["degree_centrality"], 0.0)
        self.assertEqual(m["betweenness_centrality"], 0.0)
        self.assertEqual(m["closeness_centrality"], 0.0)
        self.assertEqual(m["pagerank"], 1.0)
        self.assertEqual(m["clustering_coefficient"], 0.0)

    def test_star_graph_centrality(self):
        """Undirected Star Graph K_1,4: Center has maximal centralities, leaves have 0 betweenness."""
        g = SNAEngine(directed=False)
        center = "Center"
        leaves = ["L1", "L2", "L3", "L4"]
        for leaf in leaves:
            g.add_edge(center, leaf)

        summary = g.compute_network_summary()
        self.assertEqual(summary.node_count, 5)
        self.assertEqual(summary.edge_count, 4)
        self.assertEqual(summary.connected_components_count, 1)

        m_center = summary.node_metrics[center]
        self.assertEqual(m_center["degree"], 4)
        self.assertEqual(m_center["degree_centrality"], 1.0)
        self.assertEqual(m_center["betweenness_centrality"], 1.0)
        self.assertEqual(m_center["closeness_centrality"], 1.0)

        for leaf in leaves:
            m_leaf = summary.node_metrics[leaf]
            self.assertEqual(m_leaf["degree"], 1)
            self.assertEqual(m_leaf["degree_centrality"], 0.25)
            self.assertEqual(m_leaf["betweenness_centrality"], 0.0)
            self.assertEqual(m_leaf["clustering_coefficient"], 0.0)

    def test_path_graph_betweenness(self):
        """Path Graph P_4: A - B - C - D. Internal nodes B and C have higher betweenness than endpoints."""
        g = SNAEngine(directed=False)
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("C", "D")

        summary = g.compute_network_summary()
        m = summary.node_metrics

        # Endpoints A and D cannot lie on shortest paths between any other pairs
        self.assertEqual(m["A"]["betweenness_centrality"], 0.0)
        self.assertEqual(m["D"]["betweenness_centrality"], 0.0)
        # B and C must be strictly positive and equal by symmetry
        self.assertGreater(m["B"]["betweenness_centrality"], 0.0)
        self.assertEqual(m["B"]["betweenness_centrality"], m["C"]["betweenness_centrality"])

    def test_triangle_clique(self):
        """Complete Triangle K_3: All clustering coefficients are 1.0, degree centralities 1.0."""
        g = SNAEngine(directed=False)
        g.add_edge("X", "Y")
        g.add_edge("Y", "Z")
        g.add_edge("Z", "X")

        summary = g.compute_network_summary()
        self.assertEqual(summary.average_clustering_coefficient, 1.0)
        for node in ["X", "Y", "Z"]:
            self.assertEqual(summary.node_metrics[node]["degree_centrality"], 1.0)
            self.assertEqual(summary.node_metrics[node]["clustering_coefficient"], 1.0)
            self.assertEqual(summary.node_metrics[node]["betweenness_centrality"], 0.0)

    def test_directed_in_out_degrees(self):
        """Directed chain A -> B -> C: strictly validates in-degree and out-degree asymmetry."""
        g = SNAEngine(directed=True)
        g.add_edge("A", "B")
        g.add_edge("B", "C")

        summary = g.compute_network_summary()
        m = summary.node_metrics

        self.assertEqual(m["A"]["in_degree"], 0)
        self.assertEqual(m["A"]["out_degree"], 1)

        self.assertEqual(m["B"]["in_degree"], 1)
        self.assertEqual(m["B"]["out_degree"], 1)

        self.assertEqual(m["C"]["in_degree"], 1)
        self.assertEqual(m["C"]["out_degree"], 0)

    def test_pagerank_probability_distribution(self):
        """PageRank distribution sums up to 1.0 across the network."""
        g = SNAEngine(directed=True)
        g.add_edge("P1", "P2")
        g.add_edge("P2", "P3")
        g.add_edge("P3", "P1")
        g.add_edge("P3", "P4")

        summary = g.compute_network_summary()
        total_pr = sum(m["pagerank"] for m in summary.node_metrics.values())
        self.assertAlmostEqual(total_pr, 1.0, places=4)


class TestSNAServiceAndDatabase(unittest.TestCase):
    """Integration test suite for SNAService using SQLite in-memory database."""

    def setUp(self):
        # Test isolation: in-memory SQLite created through the fail-closed guard.
        self.engine = make_test_engine("sqlite://")
        self.session_factory = make_session_factory(self.engine)
        self.db = self.session_factory()
        self.service = SNAService(algorithm_version="NativeSNAEngine-Test-v1.0")

        # Seed minimal archive document hierarchy
        self.doc = Document(
            id=1,
            filename="Factory_Diary_1792.pdf",
            original_filename="Factory_Diary_1792.pdf",
            file_path="storage/test.pdf",
            file_type=DocumentType.PDF,
            file_size=1024,
            status=DocumentStatus.COMPLETED,
        )
        self.page = Page(id=1, document_id=1, page_number=1)
        self.block1 = Block(
            id=101,
            page_id=1,
            block_index=0,
            bbox=[0, 0, 100, 100],
            ocr_text_raw="Howqua met Browne regarding tea bond.",
        )
        self.block2 = Block(
            id=102,
            page_id=1,
            block_index=1,
            bbox=[0, 100, 100, 200],
            ocr_text_raw="Mowqua guaranteed Munqua debts.",
        )
        self.db.add_all([self.doc, self.page, self.block1, self.block2])

        # Seed Persons
        self.p1 = Person(
            id=1,
            display_name="Howqua",
            canonical_name_en="Howqua",
            canonical_name_zh="娴╁畼",
            identity_status=IdentityStatus.ACCEPTED,
        )
        self.p2 = Person(
            id=2,
            display_name="Henry Browne",
            canonical_name_en="Henry Browne",
            identity_status=IdentityStatus.ACCEPTED,
        )
        self.p3 = Person(
            id=3,
            display_name="Mowqua",
            canonical_name_en="Mowqua",
            identity_status=IdentityStatus.ACCEPTED,
        )
        self.p4_provisional = Person(
            id=4,
            display_name="Uncorroborated Clerk",
            identity_status=IdentityStatus.PROVISIONAL,
        )
        self.p5_rejected = Person(
            id=5,
            display_name="Ghost False Entity",
            identity_status=IdentityStatus.REJECTED,
        )
        self.p6_isolated = Person(
            id=6,
            display_name="Solitary Merchant",
            identity_status=IdentityStatus.ACCEPTED,
        )
        self.db.add_all([self.p1, self.p2, self.p3, self.p4_provisional, self.p5_rejected, self.p6_isolated])
        self.db.commit()

        # Seed Relationships
        self.r1 = Relationship(
            id=1,
            source_person_id=1,
            target_person_id=2,
            relation_type=RelationType.COMMERCIAL,
            directionality=Directionality.DIRECTED,
            valid_from=date(1792, 4, 1),
            valid_to=date(1792, 10, 1),
            confidence=0.95,
            claim_status=ClaimStatus.ACCEPTED,
        )
        self.r2 = Relationship(
            id=2,
            source_person_id=3,
            target_person_id=1,
            relation_type=RelationType.GUARANTEE,
            directionality=Directionality.DIRECTED,
            valid_from=date(1793, 1, 1),
            valid_to=date(1793, 6, 1),
            confidence=0.85,
            claim_status=ClaimStatus.ACCEPTED,
        )
        self.r3_self_loop = Relationship(
            id=3,
            source_person_id=1,
            target_person_id=1,
            relation_type=RelationType.OTHER,
            claim_status=ClaimStatus.ACCEPTED,
        )
        self.db.add_all([self.r1, self.r2, self.r3_self_loop])
        self.db.commit()

        # Seed RelationshipEvidence
        self.ev1 = RelationshipEvidence(
            id=1,
            relationship_id=1,
            block_id=101,
            quote_text="Howqua met Browne regarding tea bond.",
            evidence_role=EvidenceRole.SUPPORTS,
        )
        self.db.add(self.ev1)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        drop_test_schema(self.engine)

    def test_default_accepted_entities_filter(self):
        """Default filter includes ACCEPTED persons and excludes PROVISIONAL and REJECTED."""
        summary = self.service.get_network_summary(self.db, SNAGraphFilterParams())
        # Accepted persons: p1, p2, p3, p6 (total 4)
        self.assertEqual(summary.node_count, 4)
        self.assertEqual(summary.isolated_nodes_count, 1)  # p6 is isolated
        self.assertEqual(summary.edge_count, 2)  # r1 and r2 (r3 is self loop ignored in edges)

    def test_include_provisional_filter(self):
        """Setting include_provisional=True includes provisional persons."""
        filters = SNAGraphFilterParams(include_provisional=True)
        summary = self.service.get_network_summary(self.db, filters)
        # Accepted (4) + Provisional (1) = 5 (Rejected still excluded)
        self.assertEqual(summary.node_count, 5)

    def test_exclude_isolates_filter(self):
        """Setting include_isolates=False prunes zero-degree nodes."""
        filters = SNAGraphFilterParams(include_isolates=False)
        summary = self.service.get_network_summary(self.db, filters)
        # Only connected nodes p1, p2, p3
        self.assertEqual(summary.node_count, 3)
        self.assertEqual(summary.isolated_nodes_count, 0)

    def test_relation_type_filtering(self):
        """Filtering by relation_types excludes non-matching edges."""
        filters = SNAGraphFilterParams(relation_types=[RelationType.COMMERCIAL])
        summary = self.service.get_network_summary(self.db, filters)
        self.assertEqual(summary.edge_count, 1)  # Only r1 matches

    def test_temporal_date_filtering(self):
        """Relationships outside period_start / period_end boundaries are filtered out."""
        # Query 1792 only
        filters_1792 = SNAGraphFilterParams(
            period_start=date(1792, 1, 1),
            period_end=date(1792, 12, 31),
        )
        summary = self.service.get_network_summary(self.db, filters_1792)
        self.assertEqual(summary.edge_count, 1)  # r1 (1792-04 to 1792-10)

        # Query 1793 only
        filters_1793 = SNAGraphFilterParams(
            period_start=date(1793, 1, 1),
            period_end=date(1793, 12, 31),
        )
        summary_1793 = self.service.get_network_summary(self.db, filters_1793)
        self.assertEqual(summary_1793.edge_count, 1)  # r2 (1793-01 to 1793-06)

    def test_person_neighbors_and_evidence_traceability(self):
        """Querying person neighbors retrieves connected entities and links back to block_id."""
        neighbors_res = self.service.get_person_neighbors(self.db, person_id=1)
        self.assertEqual(neighbors_res.person_id, 1)
        self.assertEqual(neighbors_res.neighbor_count, 2)  # Henry Browne (out) and Mowqua (in)

        # Check Browne neighbor
        browne_item = next(n for n in neighbors_res.neighbors if n.person_id == 2)
        self.assertEqual(browne_item.direction, "out")
        self.assertEqual(browne_item.relation_count, 1)
        self.assertIn("commercial", browne_item.relation_types)
        self.assertEqual(browne_item.evidence_count, 1)
        self.assertEqual(browne_item.evidence_block_ids, [101])
        self.assertEqual(browne_item.evidences[0].block_id, 101)
        self.assertIn("Howqua met Browne", browne_item.evidences[0].quote_text)

    def test_person_metrics_exclusion_note(self):
        """Querying an excluded person returns in_network=False with informative note."""
        # Provisional person under default filters
        m_provisional = self.service.get_person_metrics(self.db, person_id=4)
        self.assertFalse(m_provisional.in_network)
        self.assertIsNotNone(m_provisional.note)
        self.assertIn("PROVISIONAL", m_provisional.note)

    def test_snapshot_creation_and_persistence(self):
        """Persisting a snapshot stores records into sna_graph_metrics table."""
        req = SNASnapshotCreateRequest(
            run_id="test_run_snapshot_001",
            graph_scope="test_canton_scope",
        )
        res = self.service.create_and_persist_snapshot(self.db, req)
        self.assertEqual(res.run_id, "test_run_snapshot_001")
        self.assertEqual(res.metrics_persisted_count, 4)

        # Verify database record
        persisted = (
            self.db.query(SNAGraphMetric)
            .filter(SNAGraphMetric.run_id == "test_run_snapshot_001")
            .all()
        )
        self.assertEqual(len(persisted), 4)
        howqua_metric = next(m for m in persisted if m.person_id == 1)
        self.assertIsNotNone(howqua_metric.degree_centrality)
        self.assertIsNotNone(howqua_metric.betweenness_centrality)
        self.assertEqual(howqua_metric.graph_scope, "test_canton_scope")
        self.assertEqual(howqua_metric.algorithm_version, "NativeSNAEngine-Test-v1.0")


class TestSNAAPIEndpoints(unittest.TestCase):
    """End-to-end HTTP API testing for Stage 4 SNA endpoints."""

    @classmethod
    def setUpClass(cls):
        # Dedicated engine and session for API test suite (guard validated,
        # in-memory SQLite; never the application engine).
        cls.api_engine = make_test_engine("sqlite://")
        cls.api_session_factory = make_session_factory(cls.api_engine)

        def api_override_get_db():
            db = cls.api_session_factory()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = api_override_get_db
        cls.client = TestClient(app)

        # Seed initial test entities in API database
        db = cls.api_session_factory()
        cls.p1 = Person(
            id=10,
            display_name="Consqua",
            identity_status=IdentityStatus.ACCEPTED,
        )
        cls.p2 = Person(
            id=20,
            display_name="Exchin",
            identity_status=IdentityStatus.ACCEPTED,
        )
        db.add_all([cls.p1, cls.p2])
        db.commit()

        cls.r1 = Relationship(
            id=100,
            source_person_id=10,
            target_person_id=20,
            relation_type=RelationType.COMMERCIAL,
            claim_status=ClaimStatus.ACCEPTED,
        )
        db.add(cls.r1)
        db.commit()
        db.close()

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.pop(get_db, None)
        drop_test_schema(cls.api_engine)

    def test_api_network_summary(self):
        """GET /api/kg/sna/summary returns 200 with valid structure."""
        resp = self.client.get("/api/kg/sna/summary")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("node_count", data)
        self.assertIn("edge_count", data)
        self.assertIn("density", data)
        self.assertIn("average_degree", data)
        self.assertIn("filter_parameters", data)
        self.assertIn("data_limits_note", data)

    def test_api_summary_invalid_date_validation(self):
        """GET /api/kg/sna/summary with inverted date returns 400 error."""
        resp = self.client.get("/api/kg/sna/summary?period_start=1793-12-31&period_end=1792-01-01")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot be after", resp.json()["detail"])

    def test_api_person_metrics_found_and_not_found(self):
        """GET /api/kg/sna/persons/{person_id}/metrics validates both 200 and 404."""
        resp_found = self.client.get("/api/kg/sna/persons/10/metrics")
        self.assertEqual(resp_found.status_code, 200)
        self.assertTrue(resp_found.json()["in_network"])

        resp_404 = self.client.get("/api/kg/sna/persons/999999/metrics")
        self.assertEqual(resp_404.status_code, 404)

    def test_api_snapshot_lifecycle(self):
        """POST /api/kg/sna/snapshot creates snapshot and GET /api/kg/sna/snapshots retrieves it."""
        run_id = f"test_api_run_{int(datetime.utcnow().timestamp())}"
        payload = {
            "run_id": run_id,
            "graph_scope": "api_test_scope",
            "filters": {
                "include_provisional": False,
                "include_isolates": True,
            },
        }
        create_resp = self.client.post("/api/kg/sna/snapshot", json=payload)
        self.assertEqual(create_resp.status_code, 201)
        data = create_resp.json()
        self.assertEqual(data["run_id"], run_id)
        self.assertEqual(data["graph_scope"], "api_test_scope")
        self.assertEqual(data["node_count"], 2)

        # List snapshots
        list_resp = self.client.get("/api/kg/sna/snapshots?limit=5")
        self.assertEqual(list_resp.status_code, 200)
        runs = [item["run_id"] for item in list_resp.json()]
        self.assertIn(run_id, runs)

        # Get specific run metrics
        metrics_resp = self.client.get(f"/api/kg/sna/snapshots/{run_id}")
        self.assertEqual(metrics_resp.status_code, 200)
        self.assertEqual(len(metrics_resp.json()), 2)


if __name__ == "__main__":
    unittest.main()
