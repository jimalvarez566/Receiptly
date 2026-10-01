"""Field-level scoring for receipt extraction.

Design rules that matter for interpreting the numbers:

* A ``None`` *label* means "not annotated" and the field is skipped entirely —
  it never counts as a miss. Only fields with a ground-truth value are scored,
  and every reported rate carries its own denominator so partial annotation
  can't silently inflate a score.
* A ``None`` *prediction* against a non-null label is a miss, and is reported
  separately from a wrong value. Distinguishing "found nothing" from "found the
  wrong thing" matters here: a missing total is a blank field in the reviewer
  UI, while a wrong total silently feeds the fraud scorer.
* Merchant uses normalised comparison plus character error rate, so an engine
  that returns "ACME MART." instead of "Acme Mart" is not scored the same as one
  that returns a tagline or address line.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from eval.datasets import REPO_ROOT

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")

# Amounts within this many dollars are considered the same value.
AMOUNT_TOLERANCE = Decimal("0.01")


def normalize_text(s: str | None) -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _PUNCT.sub(" ", s.lower())
    return _WS.sub(" ", s).strip()


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (stdlib only — no extra dependency for the harness)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,        # deletion
                cur[j - 1] + 1,     # insertion
                prev[j - 1] + (ca != cb),  # substitution
            ))
        prev = cur
    return prev[-1]


def cer(pred: str | None, gold: str | None) -> float | None:
    """Character error rate of ``pred`` against ``gold``, after normalisation.

    Returns None when there is no gold string to measure against. Values above
    1.0 are possible (a prediction much longer than the reference) and are not
    clipped, because a wildly over-long merchant string is a real failure mode
    worth seeing in the numbers.
    """
    g = normalize_text(gold)
    if not g:
        return None
    return edit_distance(normalize_text(pred), g) / len(g)


def amounts_equal(a: Decimal | None, b: Decimal | None) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= AMOUNT_TOLERANCE


@dataclass
class FieldTally:
    """Counts for one scalar field across a dataset."""

    labelled: int = 0        # receipts where ground truth exists
    correct: int = 0         # prediction matches
    wrong: int = 0           # prediction present but different
    missing: int = 0         # prediction is None
    cer_values: list[float] = field(default_factory=list)

    def record(self, predicted_present: bool, is_correct: bool, cer_value: float | None = None) -> None:
        self.labelled += 1
        if is_correct:
            self.correct += 1
        elif not predicted_present:
            self.missing += 1
        else:
            self.wrong += 1
        if cer_value is not None:
            self.cer_values.append(cer_value)

    def as_dict(self) -> dict:
        d = {
            "labelled": self.labelled,
            "correct": self.correct,
            "wrong": self.wrong,
            "missing": self.missing,
            "accuracy": round(self.correct / self.labelled, 4) if self.labelled else None,
        }
        if self.cer_values:
            d["mean_cer"] = round(sum(self.cer_values) / len(self.cer_values), 4)
        return d


@dataclass
class ItemTally:
    """Micro-averaged precision/recall over line items."""

    true_positives: int = 0
    predicted: int = 0
    gold: int = 0
    receipts: int = 0

    def record(self, tp: int, n_pred: int, n_gold: int) -> None:
        self.true_positives += tp
        self.predicted += n_pred
        self.gold += n_gold
        self.receipts += 1

    def as_dict(self) -> dict:
        p = self.true_positives / self.predicted if self.predicted else None
        r = self.true_positives / self.gold if self.gold else None
        f1 = (2 * p * r / (p + r)) if (p and r) else (0.0 if (p is not None and r is not None) else None)
        return {
            "receipts": self.receipts,
            "gold_items": self.gold,
            "predicted_items": self.predicted,
            "matched": self.true_positives,
            "precision": round(p, 4) if p is not None else None,
            "recall": round(r, 4) if r is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
        }


def match_line_items(
    predicted: list[dict],
    gold: list[dict],
    *,
    amount_only: bool = False,
) -> int:
    """Greedily count how many gold items the prediction recovered.

    An item matches when its amount is within tolerance and (unless
    ``amount_only``) its normalised description matches the gold description.
    Each predicted item is consumed at most once, so duplicating a line cannot
    inflate recall.

    Description comparison is containment-based in either direction: OCR
    routinely truncates ("ORGANIC BANAN") or pads ("2 Coffee Beans") the item
    name, and both should count as having found the item.
    """
    remaining = list(predicted)
    matched = 0

    for g in gold:
        g_amount = _to_decimal(g.get("amount"))
        g_desc = normalize_text(g.get("description"))

        for i, p in enumerate(remaining):
            if not amounts_equal(_to_decimal(p.get("amount")), g_amount):
                continue
            if not amount_only:
                p_desc = normalize_text(p.get("description"))
                if not p_desc or not g_desc:
                    continue
                if g_desc not in p_desc and p_desc not in g_desc:
                    continue
            remaining.pop(i)
            matched += 1
            break

    return matched


def _to_decimal(v) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except Exception:
        return None


def score_receipt(predicted, gold) -> dict:
    """Score one receipt. ``predicted`` is an OCRResult, ``gold`` a LabeledReceipt."""
    out: dict = {
        "id": gold.id,
        # Repo-relative so committed results are identical on every machine
        # and don't embed anyone's home directory.
        "file": gold.file.relative_to(REPO_ROOT).as_posix(),
        "source_type": gold.source_type,
    }

    if gold.merchant is not None:
        pred_m = predicted.merchant
        out["merchant"] = {
            "gold": gold.merchant,
            "pred": pred_m,
            "correct": normalize_text(pred_m) == normalize_text(gold.merchant),
            "present": pred_m is not None,
            "cer": cer(pred_m, gold.merchant),
        }

    if gold.transaction_date is not None:
        pred_d = predicted.transaction_date
        out["date"] = {
            "gold": gold.transaction_date.isoformat(),
            "pred": pred_d.isoformat() if isinstance(pred_d, date) else None,
            "correct": pred_d == gold.transaction_date,
            "present": pred_d is not None,
        }

    if gold.total_amount is not None:
        pred_t = _to_decimal(predicted.total_amount)
        out["total"] = {
            "gold": str(gold.total_amount),
            "pred": str(pred_t) if pred_t is not None else None,
            "correct": amounts_equal(pred_t, gold.total_amount),
            "present": pred_t is not None,
        }

    if gold.line_items is not None:
        pred_items = predicted.line_items or []
        out["line_items"] = {
            "gold_count": len(gold.line_items),
            "pred_count": len(pred_items),
            "matched": match_line_items(pred_items, gold.line_items),
            "matched_amount_only": match_line_items(pred_items, gold.line_items, amount_only=True),
        }

    scored = [k for k in ("merchant", "date", "total") if k in out]
    out["all_key_fields_correct"] = bool(scored) and all(out[k]["correct"] for k in scored)
    return out


def aggregate(receipt_scores: list[dict]) -> dict:
    """Roll per-receipt scores up into dataset-level metrics."""
    tallies = {k: FieldTally() for k in ("merchant", "date", "total")}
    items = ItemTally()
    items_amount_only = ItemTally()
    full_correct = 0
    full_scored = 0

    for rs in receipt_scores:
        for key, tally in tallies.items():
            if key not in rs:
                continue
            f = rs[key]
            tally.record(f["present"], f["correct"], f.get("cer"))

        if "line_items" in rs:
            li = rs["line_items"]
            items.record(li["matched"], li["pred_count"], li["gold_count"])
            items_amount_only.record(li["matched_amount_only"], li["pred_count"], li["gold_count"])

        if any(k in rs for k in ("merchant", "date", "total")):
            full_scored += 1
            full_correct += bool(rs["all_key_fields_correct"])

    return {
        "receipts": len(receipt_scores),
        "fields": {k: t.as_dict() for k, t in tallies.items()},
        "line_items": items.as_dict(),
        "line_items_amount_only": items_amount_only.as_dict(),
        "all_key_fields_correct": {
            "scored": full_scored,
            "correct": full_correct,
            "rate": round(full_correct / full_scored, 4) if full_scored else None,
        },
    }
