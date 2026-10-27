"""File utility functions."""
import os
import uuid
import shutil
from pathlib import Path
from typing import Optional

from app.config import settings
from app.core import logger


def ensure_directories() -> None:
    """Ensure all storage directories exist."""
    dirs = [
        settings.get_upload_path(),
        settings.get_pages_path(),
        settings.get_results_path(),
        settings.get_exports_path(),
    ]
    for dir_path in dirs:
        dir_path.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Ensured directory exists: {dir_path}")


def generate_unique_filename(original_filename: str) -> str:
    """Generate a unique filename while preserving extension."""
    ext = Path(original_filename).suffix.lower()
    unique_id = uuid.uuid4().hex[:12]
    return f"{unique_id}{ext}"


def get_file_extension(filename: str) -> str:
    """Get file extension in lowercase."""
    return Path(filename).suffix.lower()


def is_pdf(filename: str) -> bool:
    """Check if file is a PDF."""
    return get_file_extension(filename) == ".pdf"


def is_image(filename: str) -> bool:
    """Check if file is an image."""
    image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".webp"}
    return get_file_extension(filename) in image_extensions


def is_docx(filename: str) -> bool:
    """Check if file is a Word document (Office Open XML).

    Only ``.docx`` is supported: the legacy binary ``.doc`` format cannot be read
    by python-docx and has no parser in this project.
    """
    return get_file_extension(filename) == ".docx"


def save_upload_file(content: bytes, filename: str) -> Path:
    """Save uploaded file content to storage."""
    ensure_directories()
    unique_filename = generate_unique_filename(filename)
    file_path = settings.get_upload_path() / unique_filename
    file_path.write_bytes(content)
    logger.info(f"Saved upload file: {file_path}")
    return file_path


def get_document_pages_dir(document_id: int) -> Path:
    """Get directory for document pages."""
    dir_path = settings.get_pages_path() / str(document_id)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def get_document_results_dir(document_id: int) -> Path:
    """Get directory for document OCR results."""
    dir_path = settings.get_results_path() / str(document_id)
    dir_path.mkdir(parents=True, exist_ok=True)
    return dir_path


def delete_document_files(document_id: int, file_path: Optional[str] = None) -> None:
    """Delete all files associated with a document."""
    # Delete upload file
    if file_path and os.path.exists(file_path):
        os.remove(file_path)
        logger.info(f"Deleted upload file: {file_path}")

    # Delete pages directory
    pages_dir = settings.get_pages_path() / str(document_id)
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
        logger.info(f"Deleted pages directory: {pages_dir}")

    # Delete results directory
    results_dir = settings.get_results_path() / str(document_id)
    if results_dir.exists():
        shutil.rmtree(results_dir)
        logger.info(f"Deleted results directory: {results_dir}")
