"""Train forecast engine v4 and store the bundle (weekly via cron, or by hand).

    python scripts/train_models.py --store              # train, save to the DB, activate
    python scripts/train_models.py --out bundle.json    # train, write a file only

Downloads ~10 years of daily bars for the S&P 500 (cached under
MODEL_CACHE_DIR, default ~/.cache/onemarket), runs the walk-forward
evaluation, refits on all data and stores the result. Takes a few minutes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.forecasting.v4.data import load_or_download, sectors, us_universe  # noqa: E402
from backend.forecasting.v4.train import train_bundle  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--store", action="store_true", help="save to the database and activate")
    ap.add_argument("--out", type=Path, help="also write the bundle JSON here")
    ap.add_argument("--cache-dir", type=Path,
                    default=Path(os.getenv("MODEL_CACHE_DIR", Path.home() / ".cache" / "onemarket")))
    ap.add_argument("--period", default="10y")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.store and not args.out:
        ap.error("nothing to do: pass --store and/or --out")

    t = time.time()
    frames = load_or_download(us_universe() + ["SPY"], args.cache_dir / f"us_{args.period}.pkl", period=args.period)
    market = frames.pop("SPY", None)
    if market is None or len(frames) < 100:
        logging.error("download failed: %d symbols, market=%s", len(frames), market is not None)
        return 2
    bundle = train_bundle(frames, market, sectors())
    summary = {
        h: {"range_coverage": r["range"]["coverage_80"], "drop_skill": r["drop_risk"]["skill"],
            "up_skill": r["up"]["skill"], "out_ic": r["out"]["ic_mean"], "out_ic_t": r["out"]["ic_t"]}
        for h, r in bundle.report["horizons"].items()
    }
    logging.info("trained %s in %.0fs: %s", bundle.version, time.time() - t, json.dumps(summary))
    if args.out:
        args.out.write_text(bundle.to_json(), encoding="utf-8")
    if args.store:
        from backend.forecasting.v4.store import save_bundle

        save_bundle(bundle, activate=True)
        logging.info("stored and activated %s", bundle.version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
