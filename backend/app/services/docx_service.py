"""Word (.docx) ingestion: paragraph text -> archival page/block rows.

A ``.docx`` already carries machine-readable text, so it must never be sent to the
OCR queue. Each paragraph becomes one block on a single logical page, which is
exactly what the vector and graph retrievers read, so the document is searchable
and citable immediately after import.

What is stored is the document's own text, verbatim. Nothing is paraphrased,
translated or generated: ``blocks.ocr_text_raw`` holds the paragraph text and
``ocr_text_corrected`` stays empty, so no reader can mistake it for an OCR
correction or a model summary.
"""
from dataclasses import dataclass
from io import BytesIO
from typing import List, Sequence

from docx import Document as DocxDocument
from sqlalchemy.orm import Session

from app.core import logger
from app.models.block import Block
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.page import Page
from app.utils import save_upload_file

#: Only ``.docx`` (Office Open XML) is readable; the legacy binary ``.doc`` is not.
DOCX_SUFFIX = ".docx"

_HEADING_PREFIXES = ("heading", "title", "subtitle")


class DocxParseError(ValueError):
    """The uploaded file is not a readable .docx package (HTTP 400)."""


@dataclass(frozen=True)
class DocxParagraph:
    """One importable text unit of the document."""

    index: int
    text: str
    style: str
    is_heading: bool


def _paragraph_style(paragraph) -> str:
    style = getattr(paragraph, "style", None)
    name = getattr(style, "name", None)
    return str(name or "")


def _is_heading(style: str) -> bool:
    return style.strip().lower().startswith(_HEADING_PREFIXES)


def extract_paragraphs(content: bytes) -> List[DocxParagraph]:
    """Return the document's paragraphs (and table rows) in reading order.

    Pure function over the uploaded bytes: no database, no file system, no model.
    Raises :class:`DocxParseError` when the payload is not a readable .docx or
    contains no text, so the caller can report a real reason instead of storing an
    empty document.
    """
    if not content:
        raise DocxParseError("Word 文件为空。")
    try:
        document = DocxDocument(BytesIO(content))
    except Exception as exc:  # noqa: BLE001 - python-docx raises several types
        raise DocxParseError("无法解析 Word(.docx) 文件：%s" % exc) from None

    paragraphs: List[DocxParagraph] = []
    for paragraph in document.paragraphs:
        text = (paragraph.text or "").strip()
        if not text:
            continue
        style = _paragraph_style(paragraph)
        paragraphs.append(
            DocxParagraph(
                index=len(paragraphs),
                text=text,
                style=style,
                is_heading=_is_heading(style),
            )
        )

    # Archival files often carry tables (contracts, lists of goods); each row is
    # kept as one text block so its content stays searchable.
    for table in document.tables:
        for row in table.rows:
            cells = [(cell.text or "").strip() for cell in row.cells]
            text = " | ".join([cell for cell in cells if cell])
            if not text:
                continue
            paragraphs.append(
                DocxParagraph(
                    index=len(paragraphs),
                    text=text,
                    style="Table",
                    is_heading=False,
                )
            )

    if not paragraphs:
        raise DocxParseError("Word 文件中没有可导入的文本（只包含空段落或图片）。")
    return paragraphs


def to_markdown(paragraphs: Sequence[DocxParagraph]) -> str:
    """Render the paragraphs as plain Markdown for the document view."""
    lines: List[str] = []
    for paragraph in paragraphs:
        if paragraph.is_heading:
            level = 2 if paragraph.style.strip().lower().startswith(("heading 2", "subtitle")) else 1
            lines.append("#" * level + " " + paragraph.text)
        else:
            lines.append(paragraph.text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def create_docx_document(db: Session, filename: str, content: bytes) -> Document:
    """Store a .docx and materialise its text as one page of blocks.

    The caller owns the transaction boundary: this commits the document, page and
    blocks together so a partially imported Word file is never searchable.
    """
    paragraphs = extract_paragraphs(content)
    stored_path = save_upload_file(content, filename)
    markdown = to_markdown(paragraphs)

    document = Document(
        filename=stored_path.name,
        original_filename=filename,
        file_path=str(stored_path),
        file_type=DocumentType.DOCX,
        file_size=len(content),
        page_count=1,
        status=DocumentStatus.COMPLETED,
        full_markdown=markdown,
    )
    db.add(document)
    db.flush()

    page = Page(
        document_id=document.id,
        page_number=1,
        markdown_content=markdown,
    )
    db.add(page)
    db.flush()

    db.add_all(
        [
            Block(
                page_id=page.id,
                block_index=paragraph.index,
                block_label="heading" if paragraph.is_heading else (
                    "table" if paragraph.style == "Table" else "paragraph"
                ),
                bbox=[0, 0, 0, 0],
                ocr_text_raw=paragraph.text,
            )
            for paragraph in paragraphs
        ]
    )
    db.commit()
    db.refresh(document)
    logger.info(
        "Word document imported: id=%s filename=%s blocks=%s",
        document.id,
        filename,
        len(paragraphs),
    )
    return document
