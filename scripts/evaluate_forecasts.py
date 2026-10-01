"""Walk-forward skill of the forecast ensemble against an honest baseline.

    python scripts/evaluate_forecasts.py --symbols 60 --horizons 1 7 21

Uses the stored daily bars in DATABASE_URL (read-only) and the same fold
scorer as the Backtest Lab (``backend.api.backtest._score_backtest_fold``:
expanding walk-forward, purge gap >= horizon, leakage guard). For every
scored point it also records the **base rate**: the share of "up" labels in
that fold's training data. Stocks rise slightly more often than they fall,
so the base rate, not a 50/50 coin, is the bar a direction model must beat.

Reported per horizon (pooled over symbols):
  brier_model     Brier score of the probabilities the app shows (calibrated)
  brier_base      Brier score of the training base rate
  skill           Brier skill score = 1 - brier_model / brier_base
                  (> 0 beats the base rate; < 0 is worse than it)
  skill_ci95      cluster bootstrap over symbols
  hit_rate        accuracy of p > 0.5 vs the base rate's majority call
  brier_blend     Brier of (1 - k) * base + k * model for a few k: shows
                  whether any share of the model's signal adds information

Caveats: the universe is today's constituents (survivorship bias) and the
stored history is short (about two years), so treat this as a sanity check.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.api.backtest import _score_backtest_fold  # noqa: E402
from backend.db.models import Instrument, PriceBar  # noqa: E402
from backend.db.session import get_session_factory  # noqa: E402
from backend.forecasting.backtesting import WalkForwardSplitter  # noqa: E402
from backend.forecasting.features.features import build_feature_bundle, direction_label  # noqa: E402


def load_frames(min_bars: int) -> dict[str, pd.DataFrame]:
    db = get_session_factory()()
    try:
        rows = db.execute(
            select(Instrument.provider_symbol, PriceBar.ts, PriceBar.open, PriceBar.high,
                   PriceBar.low, PriceBar.close, PriceBar.volume)
            .join(Instrument, Instrument.instrument_id == PriceBar.instrument_id)
            .where(PriceBar.timeframe == "1d")
        ).all()
    finally:
        db.close()
    df = pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])
    frames: dict[str, pd.DataFrame] = {}
    for symbol, g in df.groupby("symbol"):
        g = g.dropna(subset=["close"]).sort_values("ts").drop_duplicates("ts")
        if len(g) < min_bars:
            continue
        frame = g.set_index(pd.to_datetime(g["ts"], utc=True))[["open", "high", "low", "close", "volume"]]
        frames[str(symbol)] = frame.astype(float)
    return frames


def score_symbol(frame: pd.DataFrame, horizon: int, train: int, test: int, gap: int) -> list[tuple]:
    _, features = build_feature_bundle(frame)
    closes = frame["close"].loc[features.index]
    labels = direction_label(closes, horizon)
    splitter = WalkForwardSplitter(train_size=train, test_size=test, gap=gap, expanding=True)
    out = []
    for train_idx, test_idx in splitter.splits(len(features)):
        train_labels = labels.iloc[train_idx].dropna()
        if train_labels.empty:
            continue
        base = float(train_labels.mean())
        scored, _ = _score_backtest_fold(train_idx, test_idx, features, closes, labels, frame, horizon, gap)
        for _pos, label, _raw, proba in scored:
            out.append((float(label), float(proba), base))
    return out


def summarize(per_symbol: dict[str, list[tuple]], n_boot: int, seed: int) -> dict:
    def stats(symbols):
        pts = [p for s in symbols for p in per_symbol[s]]
        y = np.array([p[0] for p in pts]); pm = np.array([p[1] for p in pts]); pb = np.array([p[2] for p in pts])
        bm = float(np.mean((pm - y) ** 2)); bb = float(np.mean((pb - y) ** 2))
        return y, pm, pb, bm, bb

    symbols = [s for s, pts in per_symbol.items() if pts]
    y, pm, pb, bm, bb = stats(symbols)
    rng = random.Random(seed)
    boots = []
    for _ in range(n_boot):
        sample = [rng.choice(symbols) for _ in symbols]
        _, _, _, bm_b, bb_b = stats(sample)
        boots.append(1 - bm_b / bb_b)
    boots.sort()
    return {
        "symbols": len(symbols),
        "points": int(len(y)),
        "up_share": round(float(y.mean()), 4),
        "brier_model": round(bm, 5),
        "brier_base": round(bb, 5),
        "brier_coin": 0.25,
        "skill": round(1 - bm / bb, 4),
        "skill_ci95": [round(boots[int(0.025 * n_boot)], 4), round(boots[int(0.975 * n_boot) - 1], 4)],
        "hit_rate_model": round(float(np.mean((pm > 0.5) == (y == 1))), 4),
        "hit_rate_base": round(float(np.mean((pb > 0.5) == (y == 1))), 4),
        "mean_prob_model": round(float(pm.mean()), 4),
        # Does any of the model help? Blend p_k = (1 - k) * base + k * model;
        # k = 0 is the base rate, k = 1 is the model as shown.
        "brier_blend": {
            str(k): round(float(np.mean(((1 - k) * pb + k * pm - y) ** 2)), 5)
            for k in (0.0, 0.1, 0.25, 0.5, 1.0)
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", type=int, default=60, help="how many symbols to sample (0 = all)")
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 7, 21])
    ap.add_argument("--train", type=int, default=100)
    ap.add_argument("--test", type=int, default=21)
    ap.add_argument("--min-bars", type=int, default=400)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", help="write the JSON report here")
    args = ap.parse_args(argv)

    frames = load_frames(args.min_bars)
    names = sorted(frames)
    if args.symbols and len(names) > args.symbols:
        names = sorted(random.Random(args.seed).sample(names, args.symbols))
    gap = max(args.horizons)
    print(f"{len(names)} symbols, horizons {args.horizons}, train>={args.train}, test={args.test}, gap={gap}", flush=True)
    report: dict = {"params": vars(args) | {"gap": gap, "universe": names}, "horizons": {}}
    for h in args.horizons:
        started = time.time()
        per_symbol = {}
        for name in names:
            try:
                per_symbol[name] = score_symbol(frames[name], h, args.train, args.test, gap)
            except Exception as exc:  # report and continue; one bad series must not sink the run
                print(f"  skip {name}: {type(exc).__name__}: {exc}", flush=True)
        report["horizons"][str(h)] = summary = summarize(per_symbol, args.bootstrap, args.seed)
        print(f"h={h:>2}  {json.dumps(summary)}  ({time.time() - started:.0f}s)", flush=True)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
