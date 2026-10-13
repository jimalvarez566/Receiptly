"""Receipt OCR with switchable engines.

Each engine is a callable ``(file_path: str) -> OCRResult``. The app uses the
one named by ``settings.OCR_ENGINE``; the eval harness asks for each by name
via ``get_engine`` so they can be compared on the same receipts.
"""

import logging
from collections.abc import Callable

from app.config import settings
from app.services.ocr.common import OCRResult

logger = logging.getLogger(__name__)

ENGINES = ("tesseract", "paddle")

__all__ = ["ENGINES", "OCRResult", "extract_receipt_data", "get_engine"]


def get_engine(name: str) -> Callable[[str], OCRResult]:
    """Return the named engine. Imports lazily so Tesseract-only installs never load Paddle."""
    if name == "tesseract":
        from app.services.ocr.tesseract import extract
    elif name == "paddle":
        from app.services.ocr.paddle import extract
    else:
        raise ValueError(f"Unknown OCR engine {name!r}. Available: {', '.join(ENGINES)}")
    return extract


def extract_receipt_data(file_path: str) -> OCRResult:
    """Extract receipt fields with the configured engine.

    If a non-Tesseract engine can't be loaded at all, fall back to Tesseract
    rather than failing the upload; ``OCRResult.engine`` records which ran.
    """
    engine = settings.OCR_ENGINE
    if engine == "tesseract":
        return get_engine("tesseract")(file_path)
    try:
        return get_engine(engine)(file_path)
    except Exception:
        logger.exception("OCR engine %r unavailable; falling back to tesseract", engine)
        return get_engine("tesseract")(file_path)
