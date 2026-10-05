"""Scheduled v4 jobs: daily universe scoring and weekly retraining.

Both are CPU-bound pandas work, so the cron endpoints start them in a
child process (``python -m backend.forecasting.v4.jobs <job>``) instead of
the single API worker; a lock file keeps two runs from overlapping.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pandas as pd

from backend.market_data.frames import bars_frame, market_symbol_for

from . import features as F
from .serve import US_MICS, build_cross_section, forecast_frame
from .store import get_bundle, save_cross_section, save_scores

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[3]
LOCK_DIR = Path(os.getenv("JOB_LOCK_DIR", tempfile.gettempdir()))
JOBS = ("predict", "train")
LOCK_STALE_S = {"predict": 30 * 60, "train": 3 * 3600}
BAR_LIMIT = 520


def _lock_path(job: str) -> Path:
    return LOCK_DIR / f"onemarket-{job}.lock"


def is_running(job: str) -> bool:
    p = _lock_path(job)
    return p.exists() and time.time() - p.stat().st_mtime < LOCK_STALE_S[job]


def spawn(job: str) -> dict:
    """Start ``job`` in a child process unless one is already running."""
    if job not in JOBS:
        raise ValueError(f"unknown job {job!r}")
    if is_running(job):
        return {"job": job, "started": False, "reason": "already running"}
    _lock_path(job).write_text(str(time.time()))
    proc = subprocess.Popen(
        [sys.executable, "-m", "backend.forecasting.v4.jobs", job],
        cwd=str(ROOT), stdout=None, stderr=None, start_new_session=True,
    )
    return {"job": job, "started": True, "pid": proc.pid}


def run_predict(market=None, registry=None) -> dict:
    """Score every registry instrument for every horizon; save scores and the
    day's cross-section."""
    t0 = time.time()
    bundle = get_bundle()
    if bundle is None:
        return {"ok": False, "error": "no model installed"}
    if market is None:
        from backend.api.deps import get_market_service

        market = get_market_service()
    if registry is None:
        from backend.api.deps import get_registry

        registry = get_registry()
    instruments = [i for i in registry.all() if getattr(i, "sector", "") not in ("ETF", "Index")]
    by_symbol = {str(i.provider_symbol or i.exchange_symbol).upper(): i for i in instruments}
    log.info("predict: loading daily bars for %d instruments (model %s)", len(by_symbol), bundle.version)
    payloads = market.get_bars_many(list(by_symbol), timeframe="1d", limit=BAR_LIMIT)
    frames = {s: bars_frame(p) for s, p in payloads.items()}
    frames = {s: f for s, f in frames.items() if len(f) >= F.WARMUP}

    markets: dict[str, pd.DataFrame | None] = {}
    for mic in {str(i.exchange_mic).upper() for i in instruments}:
        msym = market_symbol_for(mic)
        if msym not in markets:
            try:
                mf = bars_frame(market.get_bars(msym, timeframe="1d", limit=BAR_LIMIT))
                markets[msym] = mf if len(mf) >= F.WARMUP else None
            except Exception:
                markets[msym] = None

    universe = {s: f for s, f in frames.items()
                if str(by_symbol[s].exchange_mic).upper() in US_MICS and s.replace(".", "-") in set(bundle.universe)}
    log.info("predict: %d instruments have a year of history; building cross-section", len(frames))
    cs = None
    try:
        cs, _ = build_cross_section(universe, markets.get("SPY"), bundle)
        save_cross_section(cs, bundle.version)
    except ValueError as exc:
        log.warning("cross-section skipped: %s", exc)

    rows, errors = [], {}
    for sym, frame in frames.items():
        inst = by_symbol[sym]
        mic = str(inst.exchange_mic).upper()
        try:
            res = forecast_frame(frame, markets.get(market_symbol_for(mic)), bundle, cs,
                                 sector=bundle.sectors.get(sym.replace(".", "-")), mic=mic)
        except Exception as exc:
            errors[sym] = f"{type(exc).__name__}: {str(exc)[:120]}"
            continue
        closes = frame["close"]
        change = float(closes.iloc[-1] / closes.iloc[-2] - 1) if len(closes) > 1 else None
        for h, r in res.items():
            rows.append({
                "symbol": sym, "horizon_days": h, "exchange_mic": mic, "as_of": r["as_of"],
                "model_version": bundle.version, "last_close": r["last_close"],
                "p_up": r["p_up"], "p_out": r["p_out"], "out_rank": r["out_rank"], "sigma": r["sigma"],
                "q10": r["quantiles"]["0.10"], "q50": r["quantiles"]["0.50"], "q90": r["quantiles"]["0.90"],
                "drawdown_prob": r["drawdown_prob"], "vol_regime": r["regime"],
                "payload": {
                    "company_name": getattr(inst, "company_name", sym), "currency": getattr(inst, "currency", None),
                    "sector": getattr(inst, "sector", None), "change_pct": change,
                    "vol_annual_forecast": r["vol_annual_forecast"],
                    "drivers": r["drivers"], "base_up": r["base_up"],
                    "quantiles": r["quantiles"],
                },
            })
    rows = [_clean(r) for r in rows]
    log.info("predict: saving %d scores (%d instruments skipped)", len(rows), len(errors))
    saved = save_scores(rows)
    return {"ok": True, "model_version": bundle.version, "symbols": len(frames), "rows": saved,
            "cross_section": cs.as_of if cs else None, "errors": errors,
            "seconds": round(time.time() - t0, 1)}


def _clean(row: dict) -> dict:
    """NaN/inf -> None so JSON and NUMERIC columns accept the row."""
    def fix(v):
        if isinstance(v, float) and not math.isfinite(v):
            return None
        if isinstance(v, dict):
            return {k: fix(x) for k, x in v.items()}
        if isinstance(v, list):
            return [fix(x) for x in v]
        return v
    return {k: fix(v) for k, v in row.items()}


def run_train() -> dict:
    import importlib.util

    spec = importlib.util.spec_from_file_location("train_models", ROOT / "scripts" / "train_models.py")
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    code = module.main(["--store"])
    return {"ok": code == 0, "exit_code": code}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    job = (argv or sys.argv[1:] or [""])[0]
    if job not in JOBS:
        print(f"usage: python -m backend.forecasting.v4.jobs {{{'|'.join(JOBS)}}}", file=sys.stderr)
        return 2
    try:
        result = run_predict() if job == "predict" else run_train()
        log.info("%s finished: %s", job, {k: v for k, v in result.items() if k != "errors"})
        if job == "predict" and result.get("errors"):
            log.info("predict skipped %d symbols", len(result["errors"]))
        if job == "train" and result.get("ok"):
            # Fresh model: rescore immediately so the screener matches it.
            run_predict()
        return 0 if result.get("ok") else 1
    finally:
        try:
            _lock_path(job).unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
