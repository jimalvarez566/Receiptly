"""Receipt OCR with PaddleOCR.

``paddle.py`` reads the text and rebuilds printed lines from box geometry;
``common.py`` parses merchant, date, total and line items from those lines.
"""

from app.services.ocr.common import OCRResult
from app.services.ocr.paddle import extract as extract_receipt_data

__all__ = ["OCRResult", "extract_receipt_data"]
