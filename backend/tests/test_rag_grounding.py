"""Regression tests for archival grounding: temporal inference, retrieval gating,
sufficiency decisions and citation enforcement.

Root cause under test (R2.2)
----------------------------
``temporal_resolver`` parsed document years with ``\\b(17\\d\\d|18\\d\\d)\\b``.
Underscores are word characters, so a real archival filename such as
``Canton_Consultations_1792.pdf`` yielded **no** year, the document was treated
as "年代未确定", and strict single-year retrieval returned an empty evidence
list — turning a fully grounded question into a refusal.

These tests are pure: no database, no engine, no network. The "database" objects
are stubs that only answer the document-listing query.
"""
import unittest

from app.schemas.rag import QueryIntentAnalysis, RagSourceEvidence
from app.services.rag import context_builder
from app.services.rag.answer_integrity import check_citation_integrity
from app.services.rag.query_analyzer import query_analyzer
from app.services.rag.temporal_resolver import YEAR_PATTERN, temporal_resolver
from app.services.rag.vector_retriever import vector_retriever


# --------------------------------------------------------------------------- #
# Minimal stubs: only the document-listing query is answered.
# --------------------------------------------------------------------------- #
class _StubQuery:
    def __init__(self, rows):
        self._rows = rows

    def options(self, *args, **kwargs):
        return self

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _StubDB:
    """Answers ``db.query(Document.id, Document.original_filename).all()``."""

    def __init__(self, document_rows=(), block_rows=()):
        self._document_rows = list(document_rows)
        self._block_rows = list(block_rows)

    def query(self, *entities, **kwargs):
        first = entities[0] if entities else None
        name = getattr(first, "__name__", str(first))
        if name == "Block":
            return _StubQuery(self._block_rows)
        return _StubQuery(self._document_rows)


def _evidence(quote: str, score: float = 0.62) -> RagSourceEvidence:
    return RagSourceEvidence(
        source_id="证据 #1",
        document_id=1,
        document_name="Canton_Consultations_1792.pdf",
        page_number=3,
        block_id=42,
        quote_text=quote,
        relevance_score=score,
        evidence_type="primary_archival",
        temporal_coverage="1792年 [filename_inferred]",
        temporal_provenance="filename_inferred",
    )


class TestYearExtraction(unittest.TestCase):
    """The year parser must work on real archival filenames, not just prose."""

    def test_underscore_joined_filename_yields_the_year(self):
        self.assertEqual(YEAR_PATTERN.findall("Canton_Consultations_1792.pdf"), ["1792"])
        self.assertEqual(YEAR_PATTERN.findall("IOR_G_12_1792.pdf"), ["1792"])

    def test_other_separators_still_work(self):
        self.assertEqual(YEAR_PATTERN.findall("Canton Consultations 1792.pdf"), ["1792"])
        self.assertEqual(YEAR_PATTERN.findall("consultations-1792-1793.pdf"), ["1792", "1793"])

    def test_longer_digit_runs_are_not_mistaken_for_years(self):
        self.assertEqual(YEAR_PATTERN.findall("117921.pdf"), [])
        self.assertEqual(YEAR_PATTERN.findall("17921"), [])

    def test_chinese_year_phrasing_is_detected(self):
        self.assertEqual(
            temporal_resolver.detect_query_years("1792年东印度公司与行商之间的毛织品贸易是如何开展的？"),
            [1792],
        )

    def test_cross_era_detection(self):
        self.assertTrue(temporal_resolver.is_cross_era_query("比较1779年与1792年的毛织品贸易"))
        self.assertFalse(temporal_resolver.is_cross_era_query("1792年的毛织品贸易"))


class TestDocumentTemporalCoverage(unittest.TestCase):
    """Coverage stays strict: only a document that really declares the year matches."""

    def test_underscore_filename_is_certain_and_covers_its_year(self):
        info = temporal_resolver.resolve_document_temporal_info(999, "Canton_Consultations_1792.pdf")
        self.assertTrue(info["is_certain"])
        self.assertEqual((info["start_year"], info["end_year"]), (1792, 1792))
        self.assertEqual(info["provenance"], "filename_inferred")
        self.assertTrue(
            temporal_resolver.is_document_covering_year(999, 1792, "Canton_Consultations_1792.pdf")
        )

    def test_a_different_year_is_still_excluded(self):
        self.assertFalse(
            temporal_resolver.is_document_covering_year(998, 1792, "Canton_Consultations_1780.pdf")
        )

    def test_unknown_document_is_still_excluded(self):
        self.assertFalse(temporal_resolver.is_document_covering_year(997, 1792, "misc_scan.pdf"))
        self.assertFalse(temporal_resolver.is_document_covering_year(997, 1792, None))

    def test_curated_override_still_wins(self):
        # 55 -> Canton Consultations 1792-1793 (human_verified)
        info = temporal_resolver.resolve_document_temporal_info(55, "irrelevant_name.pdf")
        self.assertEqual(info["provenance"], "human_verified")
        self.assertTrue(temporal_resolver.is_document_covering_year(55, 1792))

    def test_document_id_lookup_uses_the_filename(self):
        db = _StubDB([
            (1, "Canton_Consultations_1792.pdf"),
            (2, "Canton_Consultations_1780.pdf"),
            (3, "unlabelled_scan.pdf"),
        ])
        self.assertEqual(temporal_resolver.get_document_ids_for_year(db, 1792), {1})


class TestStrictYearRetrievalGate(unittest.TestCase):
    """The gate must stay closed when nothing is authorized for the target year."""

    def test_no_document_for_year_returns_no_evidence(self):
        db = _StubDB([(2, "Canton_Consultations_1780.pdf")])
        evidence = vector_retriever.retrieve_text_evidence(
            db=db,
            query="woolen contracts",
            keywords=["contracts", "woolen"],
            entities=[],
            top_k=5,
            time_filter=1792,
        )
        self.assertEqual(evidence, [])

    def test_no_blocks_returns_no_evidence(self):
        db = _StubDB([(1, "Canton_Consultations_1792.pdf")], block_rows=[])
        evidence = vector_retriever.retrieve_text_evidence(
            db=db,
            query="woolen contracts",
            keywords=["contracts", "woolen"],
            entities=[],
            top_k=5,
            time_filter=1792,
        )
        self.assertEqual(evidence, [])


class TestSufficiencyGateNotRelaxed(unittest.TestCase):
    """Fixing retrieval must not turn weak evidence into a sufficient answer."""

    def test_relevant_english_evidence_is_sufficient(self):
        intent = QueryIntentAnalysis(
            raw_query="1792年东印度公司与行商之间的毛织品贸易是如何开展的？",
            query_type="historical_analysis",
            entities=[],
            time_filter=1792,
            search_keywords=["毛织品", "woolen", "cloth", "贸易", "trade"],
        )
        context, sources, is_sufficient = context_builder.fuse_and_build_context(
            query=intent.raw_query,
            intent=intent,
            entities=[],
            relationships=[],
            graph_evidences=[],
            text_evidences=[_evidence("Howqua settled the woolen contracts for the season.")],
        )
        self.assertTrue(is_sufficient)
        self.assertEqual(len(sources), 1)
        self.assertIn("woolen contracts", context)

    def test_unrelated_evidence_is_still_insufficient(self):
        intent = QueryIntentAnalysis(
            raw_query="1792年东印度公司与行商之间的毛织品贸易是如何开展的？",
            query_type="historical_analysis",
            entities=[],
            time_filter=1792,
            search_keywords=["毛织品", "woolen", "cloth", "贸易", "trade"],
        )
        context, sources, is_sufficient = context_builder.fuse_and_build_context(
            query=intent.raw_query,
            intent=intent,
            entities=[],
            relationships=[],
            graph_evidences=[],
            text_evidences=[_evidence("The ship anchored at Whampoa on the ninth of August.", score=0.9)],
        )
        self.assertFalse(is_sufficient)
        self.assertEqual(sources, [])

    def test_no_evidence_is_insufficient(self):
        intent = QueryIntentAnalysis(
            raw_query="1792年的毛织品贸易", query_type="historical_analysis", entities=[],
            search_keywords=["woolen"],
        )
        _, sources, is_sufficient = context_builder.fuse_and_build_context(
            query=intent.raw_query, intent=intent, entities=[], relationships=[],
            graph_evidences=[], text_evidences=[],
        )
        self.assertFalse(is_sufficient)
        self.assertEqual(sources, [])


class TestQueryAnalyzerGrounding(unittest.TestCase):
    """A Chinese question must still produce English archival search terms."""

    def test_year_and_cross_lingual_keywords(self):
        intent = query_analyzer.analyze_query(
            "1792年东印度公司与行商之间的毛织品贸易是如何开展的？", db=None
        )
        self.assertEqual(intent.time_filter, 1792)
        self.assertIn("woolen", intent.search_keywords)
        self.assertIn("cloth", intent.search_keywords)
        self.assertIn("trade", intent.search_keywords)
        # NOTE: query_type is a heuristic label ("行商" makes it "factual") that
        # plays no part in retrieval or in the sufficiency gate, so it is not
        # asserted here; the retrieval-relevant outputs are.

    def test_digits_that_are_not_years_are_ignored(self):
        intent = query_analyzer.analyze_query("订单编号1179288的记录", db=None)
        self.assertIsNone(intent.time_filter)


class TestCitationIntegrityUnaffected(unittest.TestCase):
    """The post-generation citation gate must stay in force."""

    def test_fabricated_citation_number_is_invalid(self):
        check = check_citation_integrity("结论[证据 #5]。", [_evidence("x")])
        self.assertFalse(check.valid)
        self.assertEqual(check.invalid_ids, [5])

    def test_real_citation_number_is_valid(self):
        check = check_citation_integrity("结论[证据 #1]。", [_evidence("x")])
        self.assertTrue(check.valid)


if __name__ == "__main__":
    unittest.main(verbosity=2)
