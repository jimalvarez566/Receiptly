import logging
import re

import google.generativeai as genai

from app.config import settings

logger = logging.getLogger(__name__)


_PAREN_REF_RE = re.compile(r"\breceipt\s*\(\s*#\s*\d+\s*\)", re.IGNORECASE)
_PLAIN_REF_RE = re.compile(r"\breceipt\s*#\s*\d+", re.IGNORECASE)


def _redact_receipt_refs(text: str) -> str:
    """Hide other receipts' numbers from text that goes into the prompt."""
    text = _PAREN_REF_RE.sub("receipt", text)
    return _PLAIN_REF_RE.sub("another receipt", text)


def _build_prompt(receipt, flags) -> str:
    merchant = receipt.merchant or "Unknown merchant"
    amount = f"${receipt.amount:.2f}" if receipt.amount else "unknown amount"
    date = str(receipt.transaction_date) if receipt.transaction_date else "unknown date"
    score = receipt.fraud_score if receipt.fraud_score is not None else "N/A"
    risk = receipt.risk_level or "unknown"

    flag_lines = "\n".join(
        f"- [{f.severity.upper()}] {f.flag_type}: {_redact_receipt_refs(f.description or 'No description')}"
        for f in flags
    )

    return (
        f"A receipt from {merchant} for {amount} on {date} received a fraud risk "
        f"score of {score}/100 ({risk}). The following issues were detected:\n"
        f"{flag_lines}\n\n"
        "In 2-3 sentences, explain to a non-technical reviewer why this receipt was "
        "flagged and what specifically looks suspicious. Write in plain English. "
        "Do not use bullet points. Do not mention receipt numbers or IDs."
    )


def call_gemini(receipt, flags) -> str:
    """Call Gemini 2.5 Flash and return the explanation text.

    Raises ValueError if GEMINI_API_KEY is not configured.
    Raises RuntimeError if the Gemini call fails.
    """
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY is not configured")

    genai.configure(api_key=settings.GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-2.5-flash")

    prompt = _build_prompt(receipt, flags)
    logger.info("Calling Gemini for receipt %d", receipt.id)

    try:
        response = model.generate_content(prompt)
        text = response.text.strip()
        if not text:
            raise RuntimeError("Gemini returned an empty response")
        return text
    except Exception as exc:
        logger.error("Gemini call failed for receipt %d: %s", receipt.id, exc)
        raise RuntimeError(f"Gemini call failed: {exc}") from exc
