"""Tests for the read-only archival block context endpoint.

Pure: no database, no engine, no network. Block objects are lightweight
stand-ins and the repositories are patched, so the endpoint's validation order
(block → page → document membership → neighbours) is exercised directly.
"""
import unittest
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException
from sqlalchemy.exc import OperationalError

from app.api.routers import blocks as blocks_router
from app.models.block import Block
from app.services.block_context_service import (
    NEIGHBOUR_SCOPE,
    build_block_context,
    to_context_block,
)


def _block(block_id: int, index: int, page_id: int = 1, raw: str = None, corrected: str = None) -> Block:
    block = Block(
        id=block_id,
        page_id=page_id,
        block_index=index,
        block_label="paragraph",
        bbox=[0, 0, 10, 10],
        ocr_text_raw=raw if raw is not None else "raw text of block %d" % block_id,
        ocr_text_corrected=corrected,
    )
    return block


def _document(doc_id: int = 7, name: str = "Canton_Consultations_1792.pdf"):
    return SimpleNamespace(id=doc_id, filename=name, original_filename=name)


class TestBuildBlockContext(unittest.TestCase):
    """Neighbour selection is pure and never invents content."""

    def test_previous_and_next_are_taken_from_the_same_page(self):
        siblings = [_block(1, 0), _block(2, 1), _block(3, 2)]
        context = build_block_context(siblings[1], siblings, window=1)
        self.assertEqual([b.block_id for b in context["previous"]], [1])
        self.assertEqual([b.block_id for b in context["next"]], [3])

    def test_window_limits_the_number_of_neighbours(self):
        siblings = [_block(i, i - 1) for i in range(1, 7)]
        context = build_block_context(siblings[3], siblings, window=2)
        self.assertEqual([b.block_id for b in context["previous"]], [2, 3])
        self.assertEqual([b.block_id for b in context["next"]], [5, 6])

    def test_no_neighbours_at_the_page_edges(self):
        siblings = [_block(1, 0), _block(2, 1)]
        first = build_block_context(siblings[0], siblings, window=1)
        self.assertEqual(first["previous"], [])
        self.assertEqual([b.block_id for b in first["next"]], [2])
        last = build_block_context(siblings[1], siblings, window=1)
        self.assertEqual([b.block_id for b in last["previous"]], [1])
        self.assertEqual(last["next"], [])

    def test_zero_window_returns_no_neighbours(self):
        siblings = [_block(1, 0), _block(2, 1), _block(3, 2)]
        context = build_block_context(siblings[1], siblings, window=0)
        self.assertEqual(context, {"previous": [], "next": []})

    def test_target_missing_from_list_yields_no_neighbours(self):
        siblings = [_block(1, 0), _block(2, 1)]
        context = build_block_context(_block(99, 5), siblings, window=1)
        self.assertEqual(context, {"previous": [], "next": []})

    def test_blocks_from_other_pages_are_excluded(self):
        siblings = [_block(1, 0, page_id=1), _block(2, 1, page_id=2), _block(3, 2, page_id=1)]
        target = siblings[0]
        context = build_block_context(target, siblings, window=2)
        self.assertEqual([b.block_id for b in context["next"]], [3], "page 2 must not leak in")

    def test_ordering_uses_block_index_not_input_order(self):
        siblings = [_block(3, 2), _block(1, 0), _block(2, 1)]
        target = siblings[2]
        context = build_block_context(target, siblings, window=1)
        self.assertEqual([b.block_id for b in context["previous"]], [1])
        self.assertEqual([b.block_id for b in context["next"]], [3])

    def test_model_derived_fields_are_never_exposed(self):
        block = _block(1, 0, raw="raw transcription", corrected="corrected transcription")
        block.modern_english = "MODEL GENERATED ENGLISH"
        block.translation_zh = "模型生成的中文译文"
        payload = to_context_block(block).model_dump()
        self.assertEqual(payload["raw_text"], "raw transcription")
        self.assertEqual(payload["corrected_text"], "corrected transcription")
        self.assertNotIn("MODEL GENERATED ENGLISH", str(payload))
        self.assertNotIn("模型生成的中文译文", str(payload))
        self.assertFalse(any("modern" in key or "translation" in key for key in payload))

    def test_block_without_text_is_flagged(self):
        block = _block(1, 0, raw="", corrected=None)
        payload = to_context_block(block)
        self.assertFalse(payload.has_text)
        self.assertEqual(payload.raw_text, "")


class _RepoStub:
    """Patches the three repositories used by the endpoint."""

    def __init__(self, block=None, page=None, document=None, siblings=None, error=None):
        self.block = block
        self.page = page
        self.document = document
        self.siblings = siblings if siblings is not None else []
        self.error = error

    def __enter__(self):
        class BlockRepo:
            def __init__(self, db):
                pass

            def get_by_id(self, block_id):
                if self_outer.error:
                    raise self_outer.error
                return self_outer.block

            def get_by_page(self, page_id):
                return self_outer.siblings

        class PageRepo:
            def __init__(self, db):
                pass

            def get_by_id(self, page_id):
                return self_outer.page

        class DocumentRepo:
            def __init__(self, db):
                pass

            def get_by_id(self, doc_id):
                return self_outer.document

        self_outer = self
        self._patches = [
            mock.patch.object(blocks_router, "BlockRepository", BlockRepo),
            mock.patch.object(blocks_router, "PageRepository", PageRepo),
            mock.patch.object(blocks_router, "DocumentRepository", DocumentRepo),
        ]
        for patcher in self._patches:
            patcher.start()
        return self

    def __exit__(self, *exc_info):
        for patcher in self._patches:
            patcher.stop()
        return False


class TestBlockContextEndpoint(unittest.TestCase):
    """Validation order: block → page → document membership."""

    def _page(self, page_id=1, document_id=7, number=3):
        return SimpleNamespace(id=page_id, document_id=document_id, page_number=number)

    def test_returns_block_text_and_neighbours(self):
        target = _block(2, 1, raw="... settled the woolen contracts ...", corrected="corrected woolen text")
        siblings = [_block(1, 0), target, _block(3, 2)]
        with _RepoStub(block=target, page=self._page(), document=_document(), siblings=siblings):
            response = blocks_router.get_block_context(block_id=2, document_id=7, window=1, db=None)

        self.assertEqual(response.document_id, 7)
        self.assertEqual(response.document_name, "Canton_Consultations_1792.pdf")
        self.assertEqual(response.page_number, 3)
        self.assertEqual(response.block.block_id, 2)
        self.assertEqual(response.block.raw_text, "... settled the woolen contracts ...")
        self.assertEqual(response.block.corrected_text, "corrected woolen text")
        self.assertEqual([b.block_id for b in response.previous], [1])
        self.assertEqual([b.block_id for b in response.next], [3])
        self.assertEqual(response.neighbour_scope, NEIGHBOUR_SCOPE)
        self.assertTrue(any("同一页" in note for note in response.notes))
        self.assertTrue(any("不包含模型生成" in note for note in response.notes))

    def test_missing_block_is_404(self):
        with _RepoStub(block=None, page=self._page(), document=_document()):
            with self.assertRaises(HTTPException) as ctx:
                blocks_router.get_block_context(block_id=404, document_id=7, window=1, db=None)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("Block not found", ctx.exception.detail)

    def test_block_from_another_document_is_rejected_and_leaks_nothing(self):
        target = _block(5, 0, page_id=9, raw="SECRET OTHER DOCUMENT TEXT")
        with _RepoStub(block=target, page=self._page(page_id=9, document_id=8), document=_document(7)):
            with self.assertRaises(HTTPException) as ctx:
                blocks_router.get_block_context(block_id=5, document_id=7, window=1, db=None)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("does not belong to document", ctx.exception.detail)
        self.assertNotIn("SECRET OTHER DOCUMENT TEXT", str(ctx.exception.detail))

    def test_missing_page_is_404(self):
        target = _block(5, 0)
        with _RepoStub(block=target, page=None, document=_document()):
            with self.assertRaises(HTTPException) as ctx:
                blocks_router.get_block_context(block_id=5, document_id=7, window=1, db=None)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("Page", ctx.exception.detail)

    def test_missing_document_is_404(self):
        target = _block(5, 0)
        with _RepoStub(block=target, page=self._page(), document=None):
            with self.assertRaises(HTTPException) as ctx:
                blocks_router.get_block_context(block_id=5, document_id=7, window=1, db=None)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("Document not found", ctx.exception.detail)

    def test_database_failure_is_503_not_an_empty_context(self):
        error = OperationalError("SELECT ...", {}, Exception("connection reset"))
        with _RepoStub(error=error):
            with self.assertRaises(HTTPException) as ctx:
                blocks_router.get_block_context(block_id=5, document_id=7, window=1, db=None)
        self.assertEqual(ctx.exception.status_code, 503)
        self.assertIn("数据库读取失败", ctx.exception.detail)
        self.assertNotIn("connection reset", ctx.exception.detail)

    def test_single_block_page_has_no_invented_neighbours(self):
        target = _block(5, 0)
        with _RepoStub(block=target, page=self._page(), document=_document(), siblings=[target]):
            response = blocks_router.get_block_context(block_id=5, document_id=7, window=1, db=None)
        self.assertEqual(response.previous, [])
        self.assertEqual(response.next, [])

    def test_route_is_registered_as_get(self):
        paths = {
            (getattr(route, "path", ""), tuple(sorted(getattr(route, "methods", []) or [])))
            for route in blocks_router.router.routes
        }
        self.assertIn(("/blocks/{block_id}/context", ("GET",)), paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)
