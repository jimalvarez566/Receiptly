"""PaddleOCR engine.

Paddle detects text as independent boxes, not lines: on a receipt the quantity,
the item name and its price ("1", "Rainbow Roll", "$15.95") come back as three
separate detections. The shared parsers expect lines, so this module rebuilds
them from box geometry first:

* Rows — boxes whose vertical centres agree to within half a glyph height are
  one printed line, read left to right. Centres are measured after undoing the
  page's dominant tilt, so a phone photo taken at a slight angle still pairs a
  description on the left with its price on the right.
* Merchant — glyph height is measured along each box's own left/right edges
  rather than from its axis-aligned bounding box, which a tilted line inflates.
  The footer cutoff is taken relative to where the text sits, not the image
  edges, since a photo includes table and background around the receipt.
"""

import logging
import math
import statistics
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from app.config import settings
from app.services.ocr.common import (
    OCRResult,
    TextLine,
    _load_images,
    _parse_fields,
    _pick_merchant,
)

logger = logging.getLogger(__name__)

# Recognitions below this score are mostly separator rows ("*****") and
# smudges; real text on receipts scores well above it.
_MIN_SCORE = 0.5

# Two boxes are on the same row when their centres differ by less than this
# fraction of the shorter box's height.
_ROW_TOLERANCE = 0.5

_pipeline = None
_pipeline_lock = threading.Lock()


def _get_pipeline():
    """Build the PaddleOCR pipeline once and reuse it; loading models takes seconds.

    Model size comes from ``settings.OCR_PADDLE_MODEL``. Document unwarping and
    orientation models are left off: they add latency and memory, and their
    effect on receipts should be measured with the eval harness before turning
    them on.
    """
    global _pipeline
    with _pipeline_lock:
        if _pipeline is None:
            from paddleocr import PaddleOCR  # heavy import; only paid when this engine is used
            size = settings.OCR_PADDLE_MODEL
            _pipeline = PaddleOCR(
                lang="en",
                text_detection_model_name=f"PP-OCRv6_{size}_det",
                text_recognition_model_name=f"PP-OCRv6_{size}_rec",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
        return _pipeline


@dataclass
class _Box:
    text: str
    x: float        # centre, in the de-tilted frame
    y: float
    height: float   # glyph height along the box's own edges


def _page_angle(polys: list[np.ndarray]) -> float:
    """Median baseline angle of the wider boxes, in radians."""
    angles = []
    for p in polys:
        dx, dy = p[1][0] - p[0][0], p[1][1] - p[0][1]
        if math.hypot(dx, dy) > 2 * _edge_height(p):
            angles.append(math.atan2(dy, dx))
    return statistics.median(angles) if angles else 0.0


def _edge_height(poly: np.ndarray) -> float:
    """Mean length of the left (top-left → bottom-left) and right (top-right → bottom-right) edges."""
    tl, tr, br, bl = poly[:4]
    return (math.dist(tl, bl) + math.dist(tr, br)) / 2


def _to_boxes(texts: list[str], scores: list[float], polys: list[np.ndarray]) -> list[_Box]:
    keep = [(t.strip(), np.asarray(p, dtype=float)) for t, s, p in zip(texts, scores, polys)
            if t.strip() and s >= _MIN_SCORE]
    if not keep:
        return []

    angle = _page_angle([p for _, p in keep])
    cos_a, sin_a = math.cos(angle), math.sin(angle)

    boxes = []
    for text, poly in keep:
        cx, cy = poly[:, 0].mean(), poly[:, 1].mean()
        boxes.append(_Box(
            text=text,
            x=cx * cos_a + cy * sin_a,
            y=-cx * sin_a + cy * cos_a,
            height=_edge_height(poly),
        ))
    return boxes


def _group_rows(boxes: list[_Box]) -> list[list[_Box]]:
    """Group boxes into printed rows, top to bottom, each sorted left to right."""
    rows: list[list[_Box]] = []
    for box in sorted(boxes, key=lambda b: b.y):
        if rows:
            row = rows[-1]
            row_y = statistics.fmean(b.y for b in row)
            row_h = statistics.median(b.height for b in row)
            if abs(box.y - row_y) < _ROW_TOLERANCE * min(box.height, row_h):
                row.append(box)
                continue
        rows.append([box])
    return [sorted(row, key=lambda b: b.x) for row in rows]


def _to_lines(rows: list[list[_Box]]) -> list[TextLine]:
    """Join each row into a line.

    A row's height is that of its longest box, not its tallest: a short "$15.95"
    or "(1)" box beside an item name is padded taller by its glyphs and would
    otherwise make an item row outrank the store name.
    """
    return [
        TextLine(
            text=" ".join(b.text for b in row),
            top=min(b.y - b.height / 2 for b in row),
            height=max(row, key=lambda b: len(b.text)).height,
        )
        for row in rows
    ]


def _read_page(img: Image.Image) -> list[TextLine]:
    """OCR one page image and return its reconstructed lines, top to bottom."""
    # Phone photos store rotation in EXIF; Paddle takes a BGR array like OpenCV.
    rgb = ImageOps.exif_transpose(img).convert("RGB")
    bgr = np.ascontiguousarray(np.asarray(rgb)[:, :, ::-1])

    pipeline = _get_pipeline()
    with _pipeline_lock:  # one predictor shared across upload threads
        page = pipeline.predict(bgr)[0]

    boxes = _to_boxes(page["rec_texts"], page["rec_scores"], page["rec_polys"])
    return _to_lines(_group_rows(boxes))


def extract(file_path: str) -> OCRResult:
    """Run PaddleOCR on a receipt file and return structured extracted data.

    Fills the same fields as the Tesseract engine. ``raw_text`` holds the
    reconstructed rows, so it reads like a printed receipt rather than a list
    of detections.

    A failure to load Paddle itself (missing package, missing models) raises,
    so the caller can fall back to Tesseract; a failure on one file is logged
    and returns an empty result, like the Tesseract engine.
    """
    _get_pipeline()
    result = OCRResult(engine="paddle")

    try:
        images = _load_images(Path(file_path))
        if not images:
            return result

        pages = [_read_page(img) for img in images]
        raw_text = "\n".join(line.text for lines in pages for line in lines)

        # Merchant: tallest line in the upper 60 % of the first page's text
        first = pages[0]
        merchant = None
        if first:
            text_top = min(l.top for l in first)
            text_bottom = max(l.top + l.height for l in first)
            merchant = _pick_merchant(first, zone_bottom=text_top + 0.60 * (text_bottom - text_top))

        _parse_fields(result, raw_text, merchant)

    except Exception:
        logger.exception("OCR extraction failed for %s", file_path)

    return result
