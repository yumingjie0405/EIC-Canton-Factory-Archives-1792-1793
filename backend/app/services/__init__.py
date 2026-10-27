"""Services module exports.

Members are resolved lazily (PEP 562) instead of being imported eagerly: the merged V2
schema layer (``app.schemas.gpt``) imports ``app.services.correction_profiles``, so an
eager ``gpt_service`` import here turned that into a circular import
(``schemas`` -> ``services/__init__`` -> ``gpt_service`` -> ``schemas``) which broke
``from app.schemas import GPTBlockProcessingResultSchema`` at start-up.
"""
from typing import Any, Dict, Tuple

_LAZY_EXPORTS: Dict[str, Tuple[str, str]] = {
    "paddle_service": ("app.services.paddle_service", "paddle_service"),
    "PaddleOCRService": ("app.services.paddle_service", "PaddleOCRService"),
    "DocumentService": ("app.services.document_service", "DocumentService"),
    "OCRService": ("app.services.ocr_service", "OCRService"),
    "gpt_service": ("app.services.gpt_service", "gpt_service"),
    "GPTService": ("app.services.gpt_service", "GPTService"),
    "ExportService": ("app.services.export_service", "ExportService"),
}

__all__ = list(_LAZY_EXPORTS)


def __getattr__(name: str) -> Any:
    try:
        module_name, attribute = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError("module 'app.services' has no attribute %r" % name) from None

    import importlib

    value = getattr(importlib.import_module(module_name), attribute)
    globals()[name] = value
    return value