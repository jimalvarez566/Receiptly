# Receipt extraction evaluation harness

Measures how well an OCR engine fills the fields the fraud pipeline actually
consumes: `merchant`, `transaction_date`, `total_amount`, `line_items`.

It exists so that "PaddleOCR is more accurate than Tesseract" is a committed
number with a date on it, not an impression. Take a baseline **before**
changing the engine; every later run is diffed against it.

## Running

From `backend/`, with the project venv active (Python 3.12):

```bash
python -m eval.run_eval --engine tesseract --dataset own
python -m eval.run_eval --engine paddle    --dataset own
python -m eval.run_eval --engine tesseract --dataset sroie --limit 200
```

Results are written to `eval/results/<engine>-<dataset>.json` and are meant to
be committed. `--no-save` prints without writing.

## How scoring works

| Field | Match rule |
|---|---|
| merchant | case/punctuation-insensitive exact match, plus mean character error rate |
| date | exact calendar date |
| total | within $0.01 |
| line items | greedy 1:1 match on amount **and** description (containment either way, since OCR truncates names); reported as precision / recall / F1, plus an amount-only variant |

Two rules keep the numbers honest:

* A **null label means "not annotated"** and is skipped, never counted as a
  miss. Every rate prints its own denominator.
* A **missing prediction is reported separately from a wrong one**. A blank
  total is an empty field in the reviewer UI; a wrong total silently feeds the
  fraud scorer. They are not the same failure.

Results are also broken down by `source_type` (`photo` / `scan` / `synthetic`),
because clean generated receipts flatter an engine and phone photos are the
case the CPSC 491 proposal actually commits to improving.

## Datasets

### `own` and `own-local` — kept local for now
Hand-labelled receipts from `test_receipts/`. Neither the images nor their
labels are committed yet, so these sets exist only on machines that have them:

* `own` — `labels/own.json`, receipts the team owns.
* `own-local` — `own` plus `labels/own.local.json`, receipts we don't have the
  rights to publish (third-party photos, an SROIE sample).

Results for both (`results/*-own*.json`) are gitignored too, because each
result records the gold and predicted values for every receipt — that is, the
receipt's contents. Every `line_items` label was checked against the printed
subtotal, and null labels mark fields that can't be fairly scored (for example,
a store name printed only as a logo).

### `sroie` — download separately
ICDAR 2019 SROIE task 3 (~1000 scanned receipts, labelled company / date /
address / total; no line items). Place as:

```
eval/data/sroie/img/X00016469612.jpg
eval/data/sroie/entities/X00016469612.txt
```

### `cord` — download separately
CORD (Indonesian receipts, line-item level labels — our only public source for
scoring `line_items`). Place as:

```
eval/data/cord/image/receipt_00000.png
eval/data/cord/json/receipt_00000.json
```

`eval/data/` is gitignored; both sets are research-use and are cited in the
report rather than redistributed here. Results on them can be committed, since their labels are already public.

## Adding an engine

`run_eval.get_engine(name)` prefers `app.services.ocr.get_engine(name)` and
falls back to the flat `extract_receipt_data` that exists today, so baselines
taken before the `ocr.py` → `ocr/` package refactor stay reproducible after it.
An engine is any callable `(file_path: str) -> OCRResult`.
