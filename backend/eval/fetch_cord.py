"""Download CORD v2 from Hugging Face into the layout ``datasets.load_cord`` reads.

    python -m eval.fetch_cord                          # test split, 100 receipts
    python -m eval.fetch_cord --splits test validation # 200 receipts

Writes ``eval/data/cord/image/<split>_<n>.<ext>`` and ``json/<split>_<n>.json``,
where the JSON is the receipt's original CORD annotation. Only the evaluation
splits are worth fetching: nothing here trains, and train alone is ~1.8 GB.

Needs ``pyarrow`` to read the parquet files (``pip install pyarrow``); the app
itself doesn't, so it isn't in requirements.txt.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from eval.datasets import DATA_DIR

REPO = "naver-clova-ix/cord-v2"
OUT = DATA_DIR / "cord"


def _ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG"):
        return ".png"
    if data.startswith(b"\xff\xd8"):
        return ".jpg"
    raise ValueError("unrecognised image format")


def fetch(splits: list[str]) -> int:
    try:
        import pyarrow.parquet as pq
    except ImportError:
        raise SystemExit("pyarrow is required: pip install pyarrow")
    from huggingface_hub import HfApi, hf_hub_download

    files = HfApi().list_repo_files(REPO, repo_type="dataset")
    (OUT / "image").mkdir(parents=True, exist_ok=True)
    (OUT / "json").mkdir(parents=True, exist_ok=True)

    written = 0
    for split in splits:
        shards = sorted(f for f in files if f.startswith(f"data/{split}-") and f.endswith(".parquet"))
        if not shards:
            raise SystemExit(f"No {split!r} split in {REPO}.")

        n = 0
        with tempfile.TemporaryDirectory() as tmp:  # parquet is deleted once converted
            for shard in shards:
                print(f"downloading {shard} ...", flush=True)
                path = hf_hub_download(REPO, shard, repo_type="dataset", local_dir=tmp)
                table = pq.read_table(path, columns=["image", "ground_truth"])
                for row in table.to_pylist():
                    stem = f"{split}_{n:04d}"
                    image = row["image"]["bytes"]
                    (OUT / "image" / f"{stem}{_ext(image)}").write_bytes(image)
                    gt = json.loads(row["ground_truth"])
                    (OUT / "json" / f"{stem}.json").write_text(json.dumps(gt, ensure_ascii=False))
                    n += 1
        print(f"  {split}: {n} receipts")
        written += n
    return written


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--splits", nargs="+", default=["test"], help="test | validation | train")
    args = parser.parse_args(argv)
    total = fetch(args.splits)
    print(f"wrote {total} receipts to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
