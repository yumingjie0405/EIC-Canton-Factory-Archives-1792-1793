"""PostgreSQL integration tests for the archival block context endpoint.

Runs against the isolated, authorised test database only (never the product
database) and exercises the real Session/repository path, including the
document/block ownership check.
"""
import unittest

from app.test_support.bootstrap import require_isolated_test_environment

require_isolated_test_environment(__name__)

from app.api.routers import blocks as blocks_router
from app.models.block import Block
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.page import Page
from app.test_support.db_fixtures import make_session_factory, make_test_engine, requires_postgres_test_db

from fastapi import HTTPException


@requires_postgres_test_db
class TestBlockContextOnPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = make_test_engine(create_schema=True)
        cls.session_factory = make_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.db = self.session_factory()
        self.db.query(Block).delete()
        self.db.query(Page).delete()
        self.db.query(Document).delete()
        self.db.commit()

        self.document = Document(
            filename="canton_1792.pdf",
            original_filename="Canton_Consultations_1792.pdf",
            file_path="storage/uploads/canton_1792.pdf",
            file_type=DocumentType.PDF,
            file_size=1024,
            page_count=1,
            status=DocumentStatus.COMPLETED,
        )
        self.other_document = Document(
            filename="other.pdf",
            original_filename="Other_Document.pdf",
            file_path="storage/uploads/other.pdf",
            file_type=DocumentType.PDF,
            file_size=1024,
            page_count=1,
            status=DocumentStatus.COMPLETED,
        )
        self.db.add_all([self.document, self.other_document])
        self.db.flush()

        self.page = Page(document_id=self.document.id, page_number=3, width=1000, height=1400)
        self.other_page = Page(document_id=self.other_document.id, page_number=1, width=1000, height=1400)
        self.db.add_all([self.page, self.other_page])
        self.db.flush()

        self.blocks = [
            Block(
                page_id=self.page.id,
                block_index=index,
                block_label="paragraph",
                bbox=[0, 0, 100, 100],
                ocr_text_raw=text,
                ocr_text_corrected=corrected,
            )
            for index, (text, corrected) in enumerate(
                [
                    ("PREVIOUS block: the ships anchored at Whampoa.", None),
                    ("CITED block: Howqua settled the woolen contracts.", "CITED block (corrected)."),
                    ("NEXT block: tea was shipped on the 3rd.", None),
                ]
            )
        ]
        self.db.add_all(self.blocks)
        self.db.commit()
        self.cited = self.blocks[1]

    def tearDown(self):
        self.db.rollback()
        self.db.query(Block).delete()
        self.db.query(Page).delete()
        self.db.query(Document).delete()
        self.db.commit()
        self.db.close()

    def _call(self, block_id, document_id, window=1):
        return blocks_router.get_block_context(
            block_id=block_id, document_id=document_id, window=window, db=self.db
        )

    def test_returns_cited_block_text_and_neighbours(self):
        response = self._call(self.cited.id, self.document.id)
        self.assertEqual(response.document_id, self.document.id)
        self.assertEqual(response.document_name, "Canton_Consultations_1792.pdf")
        self.assertEqual(response.page_number, 3)
        self.assertEqual(response.block.block_id, self.cited.id)
        self.assertIn("woolen contracts", response.block.raw_text)
        self.assertIn("corrected", response.block.corrected_text)
        self.assertEqual([b.block_id for b in response.previous], [self.blocks[0].id])
        self.assertEqual([b.block_id for b in response.next], [self.blocks[2].id])
        self.assertEqual(response.neighbour_scope, "same_page")

    def test_model_derived_fields_are_not_returned(self):
        self.cited.modern_english = "MODEL GENERATED ENGLISH"
        self.cited.translation_zh = "模型生成的中文译文"
        self.db.commit()

        response = self._call(self.cited.id, self.document.id)
        payload = response.model_dump_json()
        self.assertNotIn("MODEL GENERATED ENGLISH", payload)
        self.assertNotIn("模型生成的中文译文", payload)
        self.assertIn("woolen contracts", payload)

    def test_cross_document_lookup_is_rejected(self):
        with self.assertRaises(HTTPException) as ctx:
            self._call(self.cited.id, self.other_document.id)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("does not belong", ctx.exception.detail)
        self.assertNotIn("woolen contracts", str(ctx.exception.detail))

    def test_block_of_other_document_is_never_returned(self):
        foreign_block = Block(
            page_id=self.other_page.id,
            block_index=0,
            block_label="paragraph",
            bbox=[0, 0, 10, 10],
            ocr_text_raw="FOREIGN SECRET TEXT",
        )
        self.db.add(foreign_block)
        self.db.commit()

        with self.assertRaises(HTTPException) as ctx:
            self._call(foreign_block.id, self.document.id)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertNotIn("FOREIGN SECRET TEXT", str(ctx.exception.detail))

    def test_missing_block_is_404(self):
        with self.assertRaises(HTTPException) as ctx:
            self._call(987654, self.document.id)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("Block not found", ctx.exception.detail)

    def test_single_block_page_has_no_fabricated_neighbours(self):
        self.db.query(Block).filter(Block.id != self.cited.id).delete()
        self.db.commit()

        response = self._call(self.cited.id, self.document.id)
        self.assertEqual(response.previous, [])
        self.assertEqual(response.next, [])

    def test_window_zero_returns_no_neighbours(self):
        response = self._call(self.cited.id, self.document.id, window=0)
        self.assertEqual(response.previous, [])
        self.assertEqual(response.next, [])

    def test_block_without_transcription_is_flagged(self):
        empty = Block(
            page_id=self.page.id,
            block_index=9,
            block_label="image",
            bbox=[0, 0, 10, 10],
            ocr_text_raw=None,
            ocr_text_corrected=None,
        )
        self.db.add(empty)
        self.db.commit()

        response = self._call(empty.id, self.document.id)
        self.assertFalse(response.block.has_text)
        self.assertIsNone(response.block.raw_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
