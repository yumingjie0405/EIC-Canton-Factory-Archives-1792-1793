"""Temporal resolver for EIC Canton Factory archival documents.
Extracts, validates, and resolves temporal coverage (start_year, end_year)
with explicit provenance: 'filename_inferred', 'content_inferred', or 'unknown'.
Strictly enforces temporal boundaries to prevent cross-era retrieval leaks.
"""
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from sqlalchemy.orm import Session

from app.models.document import Document
from app.models.page import Page
from app.models.block import Block


#: A four-digit 18th/19th-century year that is not part of a longer digit run.
#:
#: ``\b`` must NOT be used here. Underscores are word characters, so
#: ``\b(17\d\d|18\d\d)\b`` never matches a real archival filename such as
#: ``Canton_Consultations_1792.pdf`` or ``IOR_G_12_1792.pdf`` — the year was
#: silently treated as "unknown", which made strict single-year retrieval return
#: zero evidence for documents that declare their year in the filename.
YEAR_PATTERN = re.compile(r"(?<!\d)(1[78]\d{2})(?!\d)")


class TemporalResolver:
    """Resolves and enforces temporal metadata across documents and search queries."""

    # Curated historical overrides based on East India Company archival catalog (IOR/G/12)
    # Allows human verification over pure regex heuristics when needed
    CURATED_DOCUMENT_YEARS: Dict[int, Tuple[int, int, str]] = {
        4: (1779, 1779, "human_verified"),      # Canton Consultations and Letter Book, 1779
        5: (1781, 1781, "human_verified"),      # Canton Consultations, 1781
        6: (1784, 1785, "human_verified"),      # Canton Consultations, 1784-1785
        7: (1783, 1783, "human_verified"),      # Canton Consultations, 1783
        8: (1799, 1799, "human_verified"),      # Canton Consultations, 1799
        10: (1778, 1779, "human_verified"),     # Canton Consultations, 1778-1779
        11: (1779, 1780, "human_verified"),     # Canton Consultations, 1779-1780
        12: (1780, 1780, "human_verified"),     # Canton Consultations, 1780
        13: (1781, 1782, "human_verified"),     # Canton Consultations, 1781-1782
        14: (1782, 1783, "human_verified"),     # Canton Consultations, 1782-1783
        15: (1785, 1786, "human_verified"),     # Canton Consultations, 1785-1786
        16: (1786, 1787, "human_verified"),     # Canton Consultations, 1786-1787
        19: (1787, 1787, "human_verified"),     # Canton Consultations, 1787
        20: (1788, 1788, "human_verified"),     # Canton Consultations, 1788
        21: (1787, 1788, "human_verified"),     # Canton Consultations, 1787-1788
        43: (1788, 1789, "human_verified"),     # Canton Consultations, 1788-1789
        45: (1789, 1790, "human_verified"),     # Canton Consultations, 1789-1790
        53: (1790, 1791, "human_verified"),     # Canton Consultations, 1790-1791
        54: (1791, 1792, "human_verified"),     # Canton Consultations, 1791-1792
        55: (1792, 1793, "human_verified"),     # Canton Consultations, 1792-1793
        56: (1793, 1793, "human_verified"),     # Canton Consultations, 1793
        88: (1793, 1794, "human_verified"),     # Canton Consultations, 1793-1794
        89: (1794, 1795, "human_verified"),     # Canton Consultations, 1794-1795
        171: (1795, 1796, "human_verified"),    # Canton Consultations, 1795-1796
        172: (1796, 1796, "human_verified"),    # Canton Consultations, 1796
        173: (1796, 1796, "human_verified"),    # Canton Consultations, 1796
        174: (1796, 1796, "human_verified"),    # Canton Consultations, 1796
        176: (1797, 1797, "human_verified"),    # Canton Consultations, 1797
        177: (1798, 1798, "human_verified"),    # Canton Consultations, 1798
        178: (1798, 1798, "human_verified"),    # Canton Consultations, 1798
        572: (1792, 1792, "human_verified"),    # Canton Consultations 1792
    }

    def resolve_document_temporal_info(self, doc_id: int, original_filename: Optional[str] = None) -> Dict[str, Any]:
        """Resolve start_year, end_year, and confidence provenance for a given document."""
        fname = original_filename or ""
        filename_years = [int(y) for y in YEAR_PATTERN.findall(fname)]

        # 1. Curated overrides.
        #
        # The table is keyed by the primary key of the *original* archival import.
        # Database ids are not a stable document identity: after a re-seed, a
        # migration replay or a test fixture insert, the same id can belong to a
        # completely different file — and the override then silently re-labels it
        # with another volume's years. That made strict single-year retrieval drop
        # legitimate documents (and return nothing) for reasons unrelated to the
        # query. The override is therefore honoured only when the filename does not
        # contradict it; a contradicting filename wins, because it describes the
        # document that is actually stored under that id.
        curated = self.CURATED_DOCUMENT_YEARS.get(doc_id)
        if curated is not None:
            start_y, end_y, prov = curated
            agrees = not filename_years or any(start_y <= y <= end_y for y in filename_years)
            if agrees:
                label = f"{start_y}年" if start_y == end_y else f"{start_y}–{end_y}年"
                return {
                    "document_id": doc_id,
                    "start_year": start_y,
                    "end_year": end_y,
                    "temporal_label": label,
                    "provenance": prov,
                    "is_certain": True,
                }

        # 2. Check filename inference
        if filename_years:
            start_y = min(filename_years)
            end_y = max(filename_years)
            label = f"{start_y}年" if start_y == end_y else f"{start_y}–{end_y}年"
            return {
                "document_id": doc_id,
                "start_year": start_y,
                "end_year": end_y,
                "temporal_label": label,
                "provenance": "filename_inferred",
                "is_certain": True,
            }

        # 3. Unknown temporal range
        return {
            "document_id": doc_id,
            "start_year": None,
            "end_year": None,
            "temporal_label": "年代未确定",
            "provenance": "unknown",
            "is_certain": False,
        }

    def is_document_covering_year(self, doc_id: int, target_year: int, original_filename: Optional[str] = None) -> bool:
        """Return True if the document covers target_year, or False if conflicted/unknown."""
        info = self.resolve_document_temporal_info(doc_id, original_filename)
        if not info["is_certain"] or info["start_year"] is None:
            # Unknown documents should not be assumed to cover a specific target year
            return False
        return info["start_year"] <= target_year <= info["end_year"]

    def detect_query_years(self, query: str) -> List[int]:
        """Extract all 4-digit historical years mentioned in the query."""
        found = [int(y) for y in YEAR_PATTERN.findall(query or "")]
        return sorted(list(dict.fromkeys(found)))

    def is_cross_era_query(self, query: str) -> bool:
        """Detect whether a query is explicitly asking to compare or analyze across multiple distinct years."""
        years = self.detect_query_years(query)
        if len(years) >= 2:
            return True
        comparative_terms = ["比较", "演变", "变化", "混同", "差异", "跨年代", "沿革", "对比", "从17", "到17"]
        return any(term in query for term in comparative_terms) and len(years) >= 1

    def get_document_ids_for_year(self, db: Session, target_year: int) -> Set[int]:
        """Retrieve set of document IDs that are verified to cover the specified year."""
        docs = db.query(Document.id, Document.original_filename, Document.filename).all()
        matching_ids: Set[int] = set()
        for doc_id, orig_name, stored_name in docs:
            # ``original_filename`` can be empty on legacy rows; fall back to the
            # stored filename so the temporal identity check still has a name.
            name = orig_name or stored_name
            if self.is_document_covering_year(doc_id, target_year, name):
                matching_ids.add(doc_id)
        return matching_ids


temporal_resolver = TemporalResolver()
