import logging
from pathlib import Path

import pytesseract
from PIL import Image, ImageEnhance, ImageOps

from app.services.ocr.common import (
    OCRResult,
    TextLine,
    _load_images,
    _parse_fields,
    _pick_merchant,
)

logger = logging.getLogger(__name__)


def _preprocess(img: Image.Image) -> Image.Image:
    """Improve image quality before OCR to reduce recognition errors.

    Steps applied in order:
    1. Grayscale   — colour is irrelevant for text; reduces noise channels.
    2. Upscale     — Tesseract accuracy drops below ~200 DPI. Receipts are
                     narrow, so photos are often under-resolved. We target a
                     minimum width of 1000 px as a safe proxy for ~300 DPI.
    3. Autocontrast — stretches the histogram so faint ink becomes darker
                      and background becomes whiter, without manual tuning.
    4. Sharpen     — counteracts blur from phone camera photos.
    5. Binarize    — converts to pure black/white. Eliminates grey gradients
                     that confuse Tesseract's edge detector.
    """
    img = img.convert("L")

    if img.width < 1000:
        scale = 1000 / img.width
        img = img.resize(
            (int(img.width * scale), int(img.height * scale)),
            Image.LANCZOS,
        )
        logger.debug("Upscaled image to %dx%d", img.width, img.height)

    img = ImageOps.autocontrast(img, cutoff=2)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    img = img.point(lambda x: 255 if x > 128 else 0)

    return img


def _parse_merchant_by_font_size(img: Image.Image) -> str | None:
    """Identify the merchant name using Tesseract's line hierarchy.

    Builds positioned lines from Tesseract's layout and hands them to
    ``_pick_merchant``, excluding the bottom 40 % of the image.

    image_to_data level meanings: 1=page 2=block 3=paragraph 4=line 5=word
    Level-4 bounding boxes span the full line, giving reliable height comparisons.
    """
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    n = len(data["text"])

    # Pass 1: build a map of line key -> {height, top} from level-4 entries
    line_info: dict[tuple, dict] = {}
    for i in range(n):
        if data["level"][i] == 4 and data["height"][i] > 0:
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            line_info[key] = {"height": data["height"][i], "top": data["top"][i]}

    if not line_info:
        return None

    # Pass 2: collect words (level=5) for each line
    line_words: dict[tuple, list[dict]] = {}
    for i in range(n):
        if data["level"][i] != 5:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        if key not in line_info:
            continue
        text = data["text"][i].strip()
        # conf == -1 are non-word structural elements; keep everything else
        if not text or int(data["conf"][i]) < 0:
            continue
        line_words.setdefault(key, []).append(
            {"text": text, "left": data["left"][i]}
        )

    # Pass 3: join each line's words left to right and let the shared picker score them
    lines = [
        TextLine(
            text=" ".join(w["text"] for w in sorted(words, key=lambda w: w["left"])),
            top=line_info[key]["top"],
            height=line_info[key]["height"],
        )
        for key, words in line_words.items()
    ]
    return _pick_merchant(lines, zone_bottom=img.height * 0.60)


def extract(file_path: str) -> OCRResult:
    """Run Tesseract on a receipt file and return structured extracted data.

    Populates:
    - merchant: store / restaurant name
    - transaction_date: date of purchase
    - transaction_time: time of purchase (string, e.g. "2:34 PM")
    - total_amount: grand total as Decimal
    - line_items: list of {"description": str, "amount": float}
    - raw_text: full Tesseract output (stored for debugging / future use)
    - confidence: rough 0-100 score based on how many fields were extracted
    """
    result = OCRResult(engine="tesseract")

    try:
        path = Path(file_path)
        images = _load_images(path)
        if not images:
            return result

        images = [_preprocess(img) for img in images]

        # Raw text from all pages (used for date / total / items parsing)
        raw_text = "\n".join(pytesseract.image_to_string(img) for img in images)

        # Merchant: use font-size detection on the first page (where the logo lives)
        merchant = _parse_merchant_by_font_size(images[0])
        _parse_fields(result, raw_text, merchant)

    except Exception:
        logger.exception("OCR extraction failed for %s", file_path)

    return result
