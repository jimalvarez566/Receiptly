"""Engine-independent pieces of receipt extraction.

Every OCR engine turns an image into lines of text with some geometry; from
there the field parsing is the same. Keeping it here means an accuracy
difference between engines in the eval harness comes from recognition and
layout, not from two diverging copies of the same regexes.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

# Date formats to try when parsing extracted date strings
_DATE_FORMATS = [
    "%m/%d/%Y", "%m-%d-%Y", "%m/%d/%y", "%m-%d-%y",
    "%Y-%m-%d",
    "%B %d, %Y", "%b %d, %Y",
    "%B %d %Y", "%b %d %Y",
]

# Keywords that signal a total line (excluded from line items)
_TOTAL_KEYWORDS = re.compile(
    r"\b(total|subtotal|tax|tip|gratuity|balance|amount\s*due|grand\s*total)\b",
    re.IGNORECASE,
)

# Patterns that indicate a line is boilerplate, not a merchant name
_BOILERPLATE = re.compile(
    r"www\.|\.com|\.net|\.org|@"           # URLs / email
    r"|\bthank\s*you\b|\bwelcome\b|\bplease\b|\breceipt\b"
    r"|\bfeedback\b|\bsurvey\b|\bcashier\b|\bregister\b|\bterminal\b"
    r"|\bapproved\b|\bchange\b|\bdebit\b|\bcredit\b|\bvisa\b|\bmastercard\b"
    r"|\b\d{4,}\b",                         # long number sequences (IDs, phone)
    re.IGNORECASE,
)


@dataclass
class OCRResult:
    merchant: str | None = None
    transaction_date: date | None = None
    transaction_time: str | None = None
    total_amount: Decimal | None = None
    line_items: list[dict] = field(default_factory=list)
    raw_text: str = ""
    confidence: Decimal = Decimal("0")
    engine: str | None = None


@dataclass
class TextLine:
    """One reconstructed line of receipt text and where it sits on the page."""
    text: str
    top: float
    height: float


def _load_images(file_path: Path) -> list[Image.Image]:
    """Return a list of PIL Images for the file (one per page for PDFs)."""
    if file_path.suffix.lower() == ".pdf":
        try:
            from pdf2image import convert_from_path
            return convert_from_path(str(file_path))
        except ImportError:
            logger.warning("pdf2image not available; cannot process PDF %s", file_path)
            return []
    return [Image.open(file_path)]


def _pick_merchant(lines: list[TextLine], zone_bottom: float) -> str | None:
    """Pick the merchant name from positioned lines.

    Three signals combined:
    1. Line height  — merchant logos are printed in the largest font on the receipt.
    2. Boilerplate filter — strips URLs, payment info, "thank you" lines, etc.
                           before scoring, so junk lines never win the height contest.
    3. Receipt zone — merchant names appear near the top; lines starting below
                      ``zone_bottom`` (footer: totals, card info, barcodes) are excluded.
    """
    candidates = [
        line for line in lines
        if line.text and not _BOILERPLATE.search(line.text) and line.top <= zone_bottom
    ]
    if not candidates:
        return None

    max_height = max(c.height for c in candidates)

    # Among lines within 15 % of the tallest, pick the topmost
    tall = [c for c in candidates if c.height >= max_height * 0.85]
    best = min(tall, key=lambda c: c.top)

    return _clean_merchant_text(best.text)


def _clean_merchant_text(text: str) -> str | None:
    """Strip symbol noise and short garbage tokens from the extracted merchant line.

    Two rules, applied per token:
    1. No alphabetic characters at all → drop.
       Removes pure symbols ("=", "#") and standalone numbers ("12345").
       Edge case: "Big 5" loses the "5" — acceptable tradeoff.
    2. 1-2 characters that are all lowercase → drop.
       Removes OCR noise fragments like "id", "it", "or" that appear next to logos.
       Keeps legitimate short names: "BP" (uppercase), "Coca-Cola" (hyphen+letters),
       "7-Eleven" (contains alpha), "H&M" (uppercase), "AT&T" (uppercase).
    """
    clean_words = []
    for token in text.split():
        if not re.search(r"[a-zA-Z]", token):
            continue
        if len(token) <= 2 and token == token.lower():
            continue
        clean_words.append(token)
    return " ".join(clean_words) or None


def _parse_date_time(text: str) -> tuple[date | None, str | None]:
    """Extract the first recognisable date and time from the receipt text."""
    found_date: date | None = None
    found_time: str | None = None

    date_patterns = [
        r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}",
        r"\d{4}-\d{2}-\d{2}",
        r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}",
    ]
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        date_str = match.group(0).strip().rstrip(",")
        for fmt in _DATE_FORMATS:
            try:
                found_date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue
        if found_date:
            break

    time_match = re.search(
        r"\b(\d{1,2}:\d{2}(?::\d{2})?\s*(?:AM|PM|am|pm)?)\b", text
    )
    if time_match:
        found_time = time_match.group(1).strip()

    return found_date, found_time


def _parse_total(text: str) -> Decimal | None:
    """Find the grand total amount on the receipt."""
    # Prefer an explicit total keyword
    total_match = re.search(
        r"(?:total|amount\s*due|grand\s*total|balance\s*due|total\s*due)"
        r"\s*[:\-]?\s*\$?\s*(\d[\d,]*\.\d{2})",
        text,
        re.IGNORECASE,
    )
    if total_match:
        try:
            return Decimal(total_match.group(1).replace(",", ""))
        except InvalidOperation:
            pass

    # Fallback: largest dollar amount in the document (often the total)
    amounts = re.findall(r"\$?\s*(\d[\d,]*\.\d{2})", text)
    if amounts:
        try:
            return max(Decimal(a.replace(",", "")) for a in amounts)
        except InvalidOperation:
            pass

    return None


def _parse_line_items(lines: list[str]) -> list[dict]:
    """Extract individual purchased items, stopping at the receipt footer.

    The footer begins at the first line that contains a total/subtotal/tax
    keyword. Everything from that line onward (payment method, change due,
    card info, barcodes) is ignored.

    The regex allows an optional single trailing character after the price,
    which handles per-item indicators common on retail receipts:
      - "X" on Walmart receipts marks taxable items
      - "N" marks non-taxable items
    """
    items: list[dict] = []
    # Trailing \w? captures optional single-char indicators (X, N, F, etc.)
    item_pattern = re.compile(r"^(.+?)\s+\$?\s*(\d[\d,]*\.\d{2})\s*\w?\s*$")

    for line in lines:
        line = line.strip()

        # Hit the footer — stop here, do not parse any further lines
        if _TOTAL_KEYWORDS.search(line):
            break

        match = item_pattern.match(line)
        if not match:
            continue

        description = match.group(1).strip()
        try:
            items.append({
                "description": description,
                "amount": float(Decimal(match.group(2).replace(",", ""))),
            })
        except InvalidOperation:
            continue

    return items


def _parse_fields(result: OCRResult, raw_text: str, merchant: str | None) -> OCRResult:
    """Fill every field of ``result`` from the engine's text and chosen merchant."""
    result.raw_text = raw_text
    lines = [l for l in raw_text.splitlines() if l.strip()]

    result.merchant = merchant
    result.transaction_date, result.transaction_time = _parse_date_time(raw_text)
    result.total_amount = _parse_total(raw_text)
    result.line_items = _parse_line_items(lines)

    # Confidence: 25 points per successfully extracted field (max 100)
    fields_found = sum([
        result.merchant is not None,
        result.transaction_date is not None,
        result.total_amount is not None,
        len(result.line_items) > 0,
    ])
    result.confidence = Decimal(str(fields_found * 25))

    logger.info(
        "OCR complete (%s) — merchant=%r date=%s total=%s items=%d confidence=%s",
        result.engine,
        result.merchant,
        result.transaction_date,
        result.total_amount,
        len(result.line_items),
        result.confidence,
    )
    return result
