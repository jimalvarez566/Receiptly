"""Labelled receipt sets, normalised into one record type.

Three sources, deliberately kept separate in the reported metrics:

* ``own``   — the team's own receipts in ``test_receipts/``, hand-labelled.
              Small, but the only set that reflects our real input mix.
* ``sroie`` — ICDAR 2019 SROIE task 3. ~1000 scanned receipts labelled with
              company / date / address / total. No line-item labels.
* ``cord``  — CORD v1/v2. Line-item level labels, which is the only public
              source we have for scoring ``line_items``.

Neither public set ships with the repo; see eval/README.md for where to put
them. A loader for a set that isn't present raises with that instruction
rather than silently returning an empty list.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent
REPO_ROOT = BACKEND_DIR.parent
OWN_IMAGES = REPO_ROOT / "test_receipts"
DATA_DIR = EVAL_DIR / "data"

# Date formats seen across the three sources.
_DATE_FORMATS = [
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y",
]


@dataclass
class LabeledReceipt:
    id: str
    file: Path
    dataset: str
    source_type: str          # photo | scan | synthetic
    merchant: str | None = None
    transaction_date: date | None = None
    total_amount: Decimal | None = None
    line_items: list[dict] | None = None
    notes: str = ""


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _parse_amount(value) -> Decimal | None:
    if value is None:
        return None
    text = str(value).replace(",", "").replace("$", "").replace("RM", "").strip()
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _load_manifest(filename: str, dataset: str) -> list[LabeledReceipt]:
    manifest = json.loads((EVAL_DIR / "labels" / filename).read_text())
    receipts = []
    for r in manifest["receipts"]:
        path = OWN_IMAGES / r["file"]
        if not path.exists():
            raise FileNotFoundError(f"Labelled image missing: {path}")
        receipts.append(LabeledReceipt(
            id=r["id"],
            file=path,
            dataset=dataset,
            source_type=r.get("source_type", "unknown"),
            merchant=r.get("merchant"),
            transaction_date=_parse_date(r.get("transaction_date")),
            total_amount=_parse_amount(r.get("total_amount")),
            line_items=r.get("line_items"),
            notes=r.get("notes", ""),
        ))
    return receipts


def load_own() -> list[LabeledReceipt]:
    """Receipts we own and can publish — the set teammates and CI can run."""
    return _load_manifest("own.json", "own")


def load_own_local() -> list[LabeledReceipt]:
    """``own`` plus receipts we can't publish, for local-only runs.

    The extra labels live in the gitignored ``labels/own.local.json`` and point
    at images that are deliberately not committed (third-party photos, an SROIE
    sample). Results from this set are gitignored too, since they contain
    values transcribed from those receipts.
    """
    local = EVAL_DIR / "labels" / "own.local.json"
    if not local.exists():
        raise FileNotFoundError(
            f"{local.name} not found. 'own-local' only exists on machines that have "
            "the unpublished receipts; use --dataset own instead."
        )
    return load_own() + _load_manifest(local.name, "own-local")


def load_sroie() -> list[LabeledReceipt]:
    """SROIE task 3 layout: ``<root>/img/X.jpg`` + ``<root>/entities/X.txt``.

    Each entities file is JSON with company / date / address / total.
    SROIE has no line-item annotations, so ``line_items`` stays None and those
    receipts are skipped by the line-item metric rather than scored as zero.
    """
    root = DATA_DIR / "sroie"
    img_dir, ent_dir = root / "img", root / "entities"
    if not ent_dir.is_dir():
        raise FileNotFoundError(
            f"SROIE not found at {root}. See eval/README.md for the download steps."
        )

    receipts = []
    for ent_file in sorted(ent_dir.glob("*.txt")):
        image = next((p for p in (img_dir / f"{ent_file.stem}{s}" for s in (".jpg", ".jpeg", ".png")) if p.exists()), None)
        if image is None:
            continue
        try:
            ent = json.loads(ent_file.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            continue
        receipts.append(LabeledReceipt(
            id=ent_file.stem,
            file=image,
            dataset="sroie",
            source_type="scan",
            merchant=ent.get("company"),
            transaction_date=_parse_date(ent.get("date")),
            total_amount=_parse_amount(ent.get("total")),
            line_items=None,
            notes="SROIE task 3",
        ))
    return receipts


def load_cord() -> list[LabeledReceipt]:
    """CORD layout: ``<root>/image/X.png`` + ``<root>/json/X.json``.

    Line items come from ``valid_line`` entries whose category is ``menu``;
    we take ``menu.nm`` as the description and ``menu.price`` as the amount
    (the line total, not the unit price). The grand total comes from
    ``total.total_price``.

    CORD receipts are Indonesian and often crumpled — a useful stress case for
    detection, but note the merchant field is frequently absent, so merchant
    accuracy on this set will be computed over a small denominator.
    """
    root = DATA_DIR / "cord"
    img_dir, json_dir = root / "image", root / "json"
    if not json_dir.is_dir():
        raise FileNotFoundError(
            f"CORD not found at {root}. See eval/README.md for the download steps."
        )

    receipts = []
    for jf in sorted(json_dir.glob("*.json")):
        image = next((p for p in (img_dir / f"{jf.stem}{s}" for s in (".png", ".jpg", ".jpeg")) if p.exists()), None)
        if image is None:
            continue
        gt = json.loads(jf.read_text())

        items, total = [], None
        for line in gt.get("valid_line", []):
            group = line.get("category", "")
            text = " ".join(w.get("text", "") for w in line.get("words", [])).strip()
            if group == "menu.nm":
                items.append({"description": text, "amount": None})
            elif group == "menu.price" and items and items[-1]["amount"] is None:
                items[-1]["amount"] = str(_parse_amount(text) or "")
            elif group == "total.total_price" and total is None:
                total = _parse_amount(text)

        items = [i for i in items if i["amount"]]
        receipts.append(LabeledReceipt(
            id=jf.stem,
            file=image,
            dataset="cord",
            source_type="photo",
            merchant=None,
            transaction_date=None,
            total_amount=total,
            line_items=items or None,
            notes="CORD",
        ))
    return receipts


LOADERS = {
    "own": load_own,
    "own-local": load_own_local,
    "sroie": load_sroie,
    "cord": load_cord,
}


def load(name: str) -> list[LabeledReceipt]:
    try:
        loader = LOADERS[name]
    except KeyError:
        raise SystemExit(f"Unknown dataset {name!r}. Available: {', '.join(LOADERS)}")
    return loader()
