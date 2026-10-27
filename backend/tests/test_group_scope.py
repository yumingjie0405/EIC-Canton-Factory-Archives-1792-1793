"""Pure unit tests for research scope semantics and group status derivation.

No database, no network: the scope is built from plain ids and the status mapping
is a pure function.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from app.models.document import DocumentStatus
from app.models.research_group import GroupImportStatus
from app.models.task import TaskStatus
from app.services.group_scope import (
    GroupNotFound,
    GroupScope,
    filter_sources_by_document_ids,
    filter_sources_in_scope,
    load_group_scope,
    optional_group_scope,
    restrict_document_ids,
)
from app.services.research_group_service import content_identity, derive_import_status


def scope(ids=(1, 2), group_id=7, name="Canton 1792"):
    return GroupScope(group_id=group_id, group_name=name, document_ids=frozenset(ids))


class GroupScopeSemanticsTests(unittest.TestCase):
    def test_scope_allows_only_its_own_documents(self):
        s = scope()
        self.assertTrue(s.allows(1))
        self.assertTrue(s.allows("2"))
        self.assertFalse(s.allows(3))
        self.assertFalse(s.allows(None))
        self.assertFalse(s.allows("not-an-id"))

    def test_empty_scope_is_empty_not_unbounded(self):
        s = scope(ids=())
        self.assertTrue(s.is_empty)
        self.assertFalse(s.allows(1))

    def test_describe_mentions_group_and_size(self):
        self.assertIn("Canton 1792", scope().describe())
        self.assertIn("2 篇文档", scope().describe())

    def test_unscoped_research_has_no_restriction(self):
        self.assertIsNone(restrict_document_ids(None, None))
        self.assertEqual(restrict_document_ids({1, 2, 3}, None), {1, 2, 3})

    def test_scope_intersects_with_existing_filters(self):
        self.assertEqual(restrict_document_ids(None, scope()), {1, 2})
        self.assertEqual(restrict_document_ids({2, 3, 4}, scope()), {2})
        self.assertEqual(restrict_document_ids({9}, scope()), set())

    def test_sources_outside_the_scope_are_dropped(self):
        sources = [
            SimpleNamespace(document_id=1),
            SimpleNamespace(document_id=3),
            SimpleNamespace(document_id=None),
            {"document_id": 2},
        ]
        kept, dropped = filter_sources_in_scope(sources, scope())
        self.assertEqual(len(kept), 2)
        self.assertEqual(len(dropped), 2)
        self.assertTrue(all(getattr(s, "document_id", None) in (1, 2) for s in kept[:1]))

    def test_unverifiable_sources_are_dropped_when_scoped(self):
        kept, dropped = filter_sources_by_document_ids(
            [SimpleNamespace(document_id=None), SimpleNamespace(document_id=5)], {5}
        )
        self.assertEqual(len(kept), 1)
        self.assertEqual(len(dropped), 1)

    def test_no_scope_keeps_everything(self):
        sources = [SimpleNamespace(document_id=None), SimpleNamespace(document_id=99)]
        kept, dropped = filter_sources_in_scope(sources, None)
        self.assertEqual((len(kept), len(dropped)), (2, 0))


class FakeQuery:
    def __init__(self, group, document_ids):
        self._group = group
        self._ids = document_ids

    def filter(self, *_args, **_kwargs):
        return self

    def one_or_none(self):
        return self._group

    def all(self):
        return [(value,) for value in self._ids]


class FakeSession:
    """Minimal stand-in for ``Session`` used only by ``load_group_scope``."""

    def __init__(self, group, document_ids):
        self._group = group
        self._ids = document_ids
        self.calls = 0

    def query(self, entity):
        self.calls += 1
        if self.calls == 1:
            return FakeQuery(self._group, [])
        return FakeQuery(None, self._ids)


class GroupScopeLoadingTests(unittest.TestCase):
    def test_unknown_group_raises(self):
        db = FakeSession(group=None, document_ids=[])
        with self.assertRaises(GroupNotFound):
            load_group_scope(db, 404)

    def test_loaded_scope_lists_member_documents(self):
        db = FakeSession(group=SimpleNamespace(id=3, name="Canton 1793"), document_ids=[5, 6])
        loaded = load_group_scope(db, 3)
        self.assertEqual(loaded.group_id, 3)
        self.assertEqual(loaded.group_name, "Canton 1793")
        self.assertEqual(loaded.document_ids, frozenset({5, 6}))

    def test_no_group_means_unscoped_without_touching_the_database(self):
        db = mock.Mock()
        self.assertIsNone(optional_group_scope(db, None))
        db.query.assert_not_called()


class ImportStatusDerivationTests(unittest.TestCase):
    def test_blocks_win_over_transient_document_state(self):
        status, _ = derive_import_status(DocumentStatus.FAILED, True, TaskStatus.FAILED)
        self.assertEqual(status, GroupImportStatus.READY)

    def test_failed_document_without_text_is_failed(self):
        status, _ = derive_import_status(DocumentStatus.FAILED, False, TaskStatus.FAILED)
        self.assertEqual(status, GroupImportStatus.FAILED)

    def test_running_task_is_processing(self):
        status, _ = derive_import_status(DocumentStatus.PROCESSING, False, TaskStatus.RUNNING)
        self.assertEqual(status, GroupImportStatus.PROCESSING)

    def test_pending_task_is_queued(self):
        status, _ = derive_import_status(DocumentStatus.PENDING, False, TaskStatus.PENDING)
        self.assertEqual(status, GroupImportStatus.QUEUED)

    def test_unknown_state_is_pending_not_ready(self):
        status, _ = derive_import_status(None, False, None)
        self.assertEqual(status, GroupImportStatus.PENDING)

    def test_ocr_completed_without_blocks_is_still_ready_by_document_state(self):
        status, _ = derive_import_status(DocumentStatus.OCR_COMPLETED, False, None)
        self.assertEqual(status, GroupImportStatus.READY)

    def test_content_identity_is_sha256(self):
        import hashlib

        payload = b"%PDF-1.4 synthetic"
        self.assertEqual(content_identity(payload), hashlib.sha256(payload).hexdigest())
        self.assertNotEqual(content_identity(payload), content_identity(payload + b"x"))


if __name__ == "__main__":
    unittest.main()
