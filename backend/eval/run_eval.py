"""Run an OCR engine over a labelled set and report field-level accuracy.

    python -m eval.run_eval --engine tesseract --dataset own
    python -m eval.run_eval --engine paddle --dataset own --out eval/results/paddle-own.json
    python -m eval.run_eval --engine tesseract --dataset sroie --limit 200

Run from the ``backend/`` directory. Results are written as JSON so a run can
be committed and diffed against a later one — the point of this harness is that
"PaddleOCR is more accurate" becomes a number with a date on it rather than an
impression.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from eval import datasets, metrics

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def get_engine(name: str):
    """Return a callable ``(file_path: str) -> OCRResult`` for the named engine.

    Written to survive the ocr.py -> ocr/ package refactor: it prefers the
    package's engine registry and falls back to the current flat module, so the
    baseline numbers taken today stay reproducible afterwards.
    """
    try:
        from app.services.ocr import get_engine as registry  # type: ignore[attr-defined]
    except ImportError:
        registry = None

    if registry is not None:
        return registry(name)

    if name != "tesseract":
        raise SystemExit(
            f"Engine {name!r} is not available yet — app.services.ocr still only "
            "provides the Tesseract implementation."
        )
    from app.services.ocr import extract_receipt_data
    return extract_receipt_data


def engine_version(name: str) -> str:
    """Record what actually produced these numbers, for the committed result file."""
    try:
        if name == "tesseract":
            import pytesseract
            return f"tesseract {pytesseract.get_tesseract_version()}"
        if name == "paddle":
            import paddleocr
            import paddle
            return f"paddleocr {paddleocr.__version__} / paddlepaddle {paddle.__version__}"
    except Exception as exc:  # version reporting must never fail a run
        return f"{name} (version unavailable: {exc})"
    return name


def run(engine_name: str, dataset_name: str, limit: int | None) -> dict:
    engine = get_engine(engine_name)
    receipts = datasets.load(dataset_name)
    if limit:
        receipts = receipts[:limit]
    if not receipts:
        raise SystemExit(f"Dataset {dataset_name!r} is empty.")

    print(f"Running {engine_name} over {len(receipts)} receipt(s) from {dataset_name}...\n")

    scores, timings = [], []
    for i, gold in enumerate(receipts, 1):
        t0 = time.perf_counter()
        predicted = engine(str(gold.file))
        elapsed = time.perf_counter() - t0
        timings.append(elapsed)

        score = metrics.score_receipt(predicted, gold)
        score["seconds"] = round(elapsed, 3)
        score["reported_confidence"] = float(predicted.confidence or 0)
        scores.append(score)

        flags = "".join(
            "." if score.get(k, {}).get("correct") else ("-" if k in score else " ")
            for k in ("merchant", "date", "total")
        )
        print(f"  [{i:>4}/{len(receipts)}] {flags}  {elapsed:5.2f}s  {gold.id}")

    summary = metrics.aggregate(scores)

    by_source: dict[str, list[dict]] = {}
    for s in scores:
        by_source.setdefault(s["source_type"], []).append(s)

    return {
        "engine": engine_name,
        "engine_version": engine_version(engine_name),
        "dataset": dataset_name,
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "latency_seconds": {
            "mean": round(statistics.fmean(timings), 3),
            "median": round(statistics.median(timings), 3),
            "max": round(max(timings), 3),
            "total": round(sum(timings), 2),
        },
        "summary": summary,
        "by_source_type": {k: metrics.aggregate(v) for k, v in sorted(by_source.items())},
        "receipts": scores,
    }


def print_report(result: dict) -> None:
    s = result["summary"]
    print(f"\n{'=' * 62}")
    print(f"{result['engine']} on {result['dataset']}  ({result['engine_version']})")
    print("=" * 62)

    print(f"\n{'field':<12} {'acc':>7} {'n':>4} {'wrong':>6} {'missing':>8} {'CER':>7}")
    print("-" * 50)
    for name, f in s["fields"].items():
        if not f["labelled"]:
            continue
        acc = f"{f['accuracy']:.1%}" if f["accuracy"] is not None else "-"
        cer_val = f"{f['mean_cer']:.3f}" if "mean_cer" in f else "-"
        print(f"{name:<12} {acc:>7} {f['labelled']:>4} {f['wrong']:>6} {f['missing']:>8} {cer_val:>7}")

    li = s["line_items"]
    if li["receipts"]:
        amt = s["line_items_amount_only"]
        print(f"\nline items   P={_pct(li['precision'])} R={_pct(li['recall'])} F1={_pct(li['f1'])}"
              f"   ({li['matched']}/{li['gold_items']} gold matched, {li['predicted_items']} predicted)")
        print(f"  amount only  P={_pct(amt['precision'])} R={_pct(amt['recall'])} F1={_pct(amt['f1'])}")

    ak = s["all_key_fields_correct"]
    if ak["scored"]:
        print(f"\nmerchant+date+total all correct: {ak['correct']}/{ak['scored']} ({_pct(ak['rate'])})")

    lat = result["latency_seconds"]
    print(f"latency: mean {lat['mean']:.2f}s, median {lat['median']:.2f}s, max {lat['max']:.2f}s")

    if len(result["by_source_type"]) > 1:
        print("\nby source type:")
        for src, agg in result["by_source_type"].items():
            r = agg["all_key_fields_correct"]
            print(f"  {src:<12} {r['correct']}/{r['scored']} key-field sets correct ({_pct(r['rate'])})")


def _pct(v) -> str:
    return f"{v:.1%}" if isinstance(v, (int, float)) else "-"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--engine", default="tesseract", help="tesseract | paddle")
    parser.add_argument("--dataset", default="own", help=f"{' | '.join(datasets.LOADERS)}")
    parser.add_argument("--limit", type=int, help="only the first N receipts")
    parser.add_argument("--out", type=Path, help="write JSON here (default: eval/results/<engine>-<dataset>.json)")
    parser.add_argument("--no-save", action="store_true", help="print only, write nothing")
    args = parser.parse_args(argv)

    result = run(args.engine, args.dataset, args.limit)
    print_report(result)

    if not args.no_save:
        out = args.out or RESULTS_DIR / f"{args.engine}-{args.dataset}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2, default=str))
        print(f"\nwrote {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
