"""Market snapshots + prediction-accuracy tests (Agent 3).

Hermetic (in-memory SQLite, stub markets, no network/Redis). Covers:
  * compression round-trip (auto + both encodings, epsilon, size reduction)
  * scoring math (hit / brier / realized return + validation)
  * confidence evolution (trailing gates + trajectory)
  * DB scoring (point-in-time, survivorship-aware, calibration linkage,
    idempotent re-runs, unscored-on-missing-data)
  * retention tiers (raw 30d / compressed 1y / 2y backstop ordering)
  * workers (capture_snapshot / score_forecasts, cadence defaults)
  * cron routes (4 new distinct paths, 6 existing preserved)
"""

from __future__ import annotations

import gzip
import random
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.db.models import (
    Base,
    CalibrationSnapshot,
    Forecast,
    ForecastAccuracy,
    Instrument,
    MarketSnapshot,
    PriceBar,
)
from backend.forecasting.accuracy import (
    SCORING_FORMULA,
    confidence_trajectory,
    score_due_forecasts,
    score_one,
    trailing_stats,
)
from backend.market_data import snapshot_store as store


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def _walk_bars(n: int = 60, seed: int = 7) -> list[dict]:
    rng = random.Random(seed)
    price = 150.0
    out: list[dict] = []
    base = datetime(2026, 5, 1, tzinfo=timezone.utc)
    for i in range(n):
        drift = rng.uniform(-0.012, 0.012)
        o = round(price, 2)
        c = round(o * (1 + drift), 2)
        h = round(max(o, c) * (1 + rng.uniform(0, 0.005)), 2)
        low = round(min(o, c) * (1 - rng.uniform(0, 0.005)), 2)
        out.append({
            "ts": (base + timedelta(days=i)).isoformat(),
            "open": o, "high": h, "low": low, "close": c,
            "volume": rng.randint(100_000, 5_000_000),
        })
        price = c
    return out


class _FakeMarket:
    def __init__(self, bars: list[dict], source: str = "yfinance") -> None:
        self._bars = bars
        self._source = source

    def get_bars(self, symbol: str, timeframe: str = "1d", limit: int = 30) -> dict:
        return {
            "bars": self._bars,
            "provenance": {"source": self._source, "as_of": _utcnow().isoformat(),
                           "quality_grade": "B", "fallback_used": False,
                           "missing_fields": []},
        }


# --- compression round-trip -------------------------------------------------


def test_compress_decompress_round_trip_auto(capsys):
    bars = _walk_bars(60)
    packed = store.compress_bars(bars)
    back = store.decompress_bars(packed["blob"], packed["encoding"])
    assert store.bars_equal_within_epsilon(bars, back)
    assert [b["ts"] for b in back] == [b["ts"] for b in bars]
    assert [b["volume"] for b in back] == [b["volume"] for b in bars]
    assert packed["compressed_bytes"] < packed["raw_bytes"]
    assert packed["size_reduction_pct"] > 0
    print(f"snapshot compression: encoding={packed['encoding']} "
          f"raw={packed['raw_bytes']}B stored={packed['compressed_bytes']}B "
          f"ratio={packed['ratio']:.3f} reduction={packed['size_reduction_pct']}%")
    out = capsys.readouterr().out
    assert "reduction=" in out


def test_round_trip_both_encodings_explicit():
    bars = _walk_bars(25, seed=99)
    rows = store._canonical_rows(bars)
    raw_blob = gzip.compress(store._raw_json_bytes(rows), compresslevel=9)
    assert store.bars_equal_within_epsilon(
        bars, store.decompress_bars(raw_blob, store.ENCODING_RAW_GZIP))
    packed = store.compress_bars(bars)
    delta_blob = gzip.compress(store._encode_delta_q(rows), compresslevel=9)
    assert store.bars_equal_within_epsilon(
        bars, store.decompress_bars(delta_blob, store.ENCODING_DELTA_Q))
    assert packed["encoding"] in store.ENCODINGS


def test_round_trip_single_bar_and_rejects():
    one = _walk_bars(1)
    packed = store.compress_bars(one)
    assert store.bars_equal_within_epsilon(one, store.decompress_bars(
        packed["blob"], packed["encoding"]))
    with pytest.raises(ValueError):
        store.compress_bars([])
    with pytest.raises(ValueError):
        store.compress_bars([{"ts": "x", "open": None, "high": 1.0,
                              "low": 1.0, "close": 1.0}])
    with pytest.raises(ValueError):
        store.decompress_bars(b"nope-not-gzip", store.ENCODING_RAW_GZIP)
    with pytest.raises(ValueError):
        store.decompress_bars(packed["blob"], "zstd-future")
    assert not store.bars_equal_within_epsilon(one, _walk_bars(2))


def test_save_load_snapshot_db_and_memory_fallback():
    store.clear_memory()
    bars = _walk_bars(30)
    # In-memory fallback (no DB): still ok, never raises.
    mem = store.save_snapshot(None, symbol="aapl", bars=bars,
                              source="yfinance", quality_grade="B",
                              provenance={"source": "yfinance"})
    assert mem["ok"] is True and mem["persisted"] is False
    loaded = store.load_latest_snapshot(None, "AAPL")
    assert loaded is not None and store.bars_equal_within_epsilon(bars, loaded["bars"])
    # Real DB persist + union mirrors populated.
    db = _session()
    try:
        saved = store.save_snapshot(db, symbol="AAPL", bars=bars,
                                    source="yfinance", quality_grade="B",
                                    provenance={"source": "yfinance"})
        assert saved["ok"] is True and saved["persisted"] is True
        assert saved["snapshot_id"]
        row = db.query(MarketSnapshot).one()
        assert row.symbol == "AAPL" and row.n_bars == 30
        assert row.size_raw == row.raw_bytes and row.size_stored == row.compressed_bytes
        assert row.encoding in ("gzip+json", "delta-q100+gzip")
        back = store.load_latest_snapshot(db, "aapl")
        assert store.bars_equal_within_epsilon(bars, back["bars"])
    finally:
        db.close()


# --- scoring math -----------------------------------------------------------


def test_scoring_formula_documented():
    assert "brier = (p - y)^2" in SCORING_FORMULA


def test_score_one_hit_and_miss():
    up = score_one(0.7, 100.0, 110.0)
    assert up == {"realized_return": pytest.approx(0.10),
                  "realized_label": 1, "hit": True,
                  "brier_contrib": pytest.approx(0.09)}
    down = score_one(0.7, 100.0, 90.0)
    assert down["realized_return"] == pytest.approx(-0.10)
    assert down["realized_label"] == 0 and down["hit"] is False
    assert down["brier_contrib"] == pytest.approx(0.49)
    # p == 0.5 boundary predicts "up".
    assert score_one(0.5, 100.0, 101.0)["hit"] is True
    assert score_one(0.49, 100.0, 101.0)["hit"] is False


def test_score_one_rejects_bad_inputs():
    for bad in (1.5, -0.1, float("nan"), "high"):
        with pytest.raises(ValueError):
            score_one(bad, 100.0, 101.0)
    for bad_base, bad_target in ((0.0, 101.0), (-3.0, 101.0),
                                 (100.0, 0.0), (100.0, float("inf"))):
        with pytest.raises(ValueError):
            score_one(0.6, bad_base, bad_target)


def test_trailing_stats_gates():
    assert trailing_stats([], []) == {"n": 0, "hit_rate": None,
                                      "brier_mean": None, "suggested": "low"}
    hot = trailing_stats([True] * 12, [0.05] * 12)
    assert hot["suggested"] == "high" and hot["hit_rate"] == 1.0
    # Sharp but inaccurate: high hit-rate gate needs the brier gate too.
    sloppy = trailing_stats([True] * 12, [0.24] * 12)
    assert sloppy["suggested"] == "moderate"
    mid = trailing_stats([True] * 7 + [False] * 5, [0.2] * 12)
    assert mid["suggested"] == "moderate"
    cold = trailing_stats([True] * 5 + [False] * 7, [0.3] * 12)
    assert cold["suggested"] == "low"
    thin = trailing_stats([True] * 5, [0.01] * 5)
    assert thin["suggested"] == "low"  # n < 10 never suggests above low


def test_confidence_trajectory_cumulative():
    rows = [{"scored_at": f"2026-0{i}-01", "hit": True, "brier_contrib": 0.04}
            for i in (1, 2, 3)]
    traj = confidence_trajectory(rows)
    assert [r["n"] for r in traj] == [1, 2, 3]
    assert all(r["suggested"] == "low" for r in traj)  # thin window
    assert confidence_trajectory([]) == []


# --- DB scoring: point-in-time, survivorship, calibration, idempotency ------


def _seed_scored_world(db, *, symbol="TEST", active=True, prob=0.7,
                       created_days_ago=30, horizon=21, target_offset=None,
                       n_bars=40, start=100.0, step=1.0):
    inst = Instrument(exchange_mic="XNAS", exchange_symbol=symbol,
                      provider_symbol=symbol, company_name=f"{symbol} Inc.",
                      currency="USD", is_active=active)
    db.add(inst)
    db.commit()
    db.refresh(inst)
    now = _utcnow()
    created = now - timedelta(days=created_days_ago)
    target = (now - timedelta(days=created_days_ago - horizon)).date() \
        if target_offset is None else (now - timedelta(days=target_offset)).date()
    for i in range(n_bars):
        ts = created - timedelta(days=2) + timedelta(days=i)
        close = start + step * i
        db.add(PriceBar(instrument_id=inst.instrument_id, ts=ts, timeframe="1d",
                        open=close - 0.5, high=close + 0.5, low=close - 1.0,
                        close=close, volume=1_000_000, source="yfinance",
                        as_of=ts, quality_grade="B"))
    fc = Forecast(instrument_id=inst.instrument_id, horizon_days=horizon,
                  target_date=target, direction_prob=prob,
                  volatility_regime="normal", confidence="moderate",
                  model_version="m1", feature_version="f1", data_version="d1",
                  provenance={}, created_at=created)
    db.add(fc)
    db.commit()
    db.refresh(fc)
    return inst, fc


def test_score_due_forecasts_persists_accuracy_and_calibration():
    db = _session()
    try:
        _, fc = _seed_scored_world(db)
        db.add(CalibrationSnapshot(
            symbol="TEST", exchange_mic="XNAS", horizon_days=21,
            model_version="m1", feature_version="f1", data_version="d1",
            brier=0.22, ece=0.05, n_windows=12, reliability=[],
            members={"momentum": {"hit_rate": 0.6, "n": 12}}))
        db.commit()
        out = score_due_forecasts(db, now=_utcnow())
        assert out["scored"] == 1 and out["unscored"] == 0 and not out["errors"]
        assert out["hits"] == 1 and out["brier_mean"] == pytest.approx(0.09)
        acc = db.query(ForecastAccuracy).one()
        assert acc.symbol == "TEST" and acc.horizon_days == 21
        assert acc.hit is True and acc.realized_label == 1
        assert float(acc.realized_return) > 0
        assert float(acc.realized_ret) == pytest.approx(float(acc.realized_return), abs=1e-6)
        assert float(acc.brier_contrib) == pytest.approx(0.09)
        assert acc.confidence_before == "moderate"
        assert acc.confidence_after == "low"  # n=1 < 10 -> low
        assert acc.forecast_id == fc.forecast_id
        # Calibration linkage: walk-forward metrics untouched, realized merged.
        snap = db.query(CalibrationSnapshot).one()
        assert float(snap.brier) == pytest.approx(0.22)
        assert snap.members["realized"]["n"] == 1
        assert snap.members["realized"]["hit_rate"] == 1.0
        assert snap.members["momentum"]["hit_rate"] == 0.6
        # Idempotent re-run scores nothing twice.
        again = score_due_forecasts(db, now=_utcnow())
        assert again["scored"] == 0
        assert db.query(ForecastAccuracy).count() == 1
    finally:
        db.close()


def test_scoring_survivorship_aware_and_point_in_time():
    db = _session()
    try:
        # Delisted instrument (is_active=False) still scores.
        _seed_scored_world(db, symbol="DEAD", active=False)
        out = score_due_forecasts(db, symbols=["DEAD"], now=_utcnow())
        assert out["scored"] == 1
        # Future target: horizon not observable -> skipped, never imputed.
        _seed_scored_world(db, symbol="FUT", created_days_ago=2,
                           horizon=21, target_offset=-19)
        out2 = score_due_forecasts(db, symbols=["FUT"], now=_utcnow())
        assert out2["scored"] == 0
        assert db.query(ForecastAccuracy).filter(
            ForecastAccuracy.symbol == "FUT").count() == 0
        # Due forecast but no bars at all -> unscored (counted, not error).
        inst = Instrument(exchange_mic="XNAS", exchange_symbol="BARE",
                          company_name="Bare Inc.", currency="USD")
        db.add(inst)
        db.commit()
        db.refresh(inst)
        db.add(Forecast(instrument_id=inst.instrument_id, horizon_days=5,
                        target_date=date(2020, 1, 10), direction_prob=0.6,
                        model_version="m", feature_version="f", data_version="d",
                        provenance={}, created_at=datetime(2020, 1, 1)))
        db.commit()
        out3 = score_due_forecasts(db, symbols=["BARE"], now=_utcnow())
        assert out3["scored"] == 0 and out3["unscored"] == 1
    finally:
        db.close()


def test_score_graceful_without_tables():
    class _DeadDB:
        def query(self, *a, **k):
            raise RuntimeError("no such table")

    out = score_due_forecasts(_DeadDB(), now=_utcnow())
    assert out["scored"] == 0 and "reason" in out


# --- retention tiers --------------------------------------------------------


def _snapshot_row(db, *, encoding, days_ago, symbol="T"):
    stamp = _utcnow() - timedelta(days=days_ago)
    db.add(MarketSnapshot(symbol=symbol, ts=stamp, timeframe="1d",
                          encoding=encoding, payload=b"fake-bytes", n_bars=10,
                          raw_bytes=100, compressed_bytes=40,
                          source="yfinance", quality_grade="B",
                          provenance={}, created_at=stamp))
    db.commit()


def test_retention_snapshot_tiers_and_backstop():
    from backend.observability.retention import purge, purge_dry_run

    db = _session()
    try:
        now = _utcnow()
        _snapshot_row(db, encoding="raw", days_ago=60)            # tier raw
        _snapshot_row(db, encoding="gzip+json", days_ago=400)     # tier compressed
        _snapshot_row(db, encoding="gzip+json", days_ago=10)      # fresh: kept
        _snapshot_row(db, encoding="delta-q100+gzip", days_ago=3 * 365)  # backstop
        dry = purge_dry_run(db, now=now)
        assert dry["counts"]["snapshots_raw"] == 1
        assert dry["counts"]["snapshots_compressed"] == 2
        assert dry["counts"]["market_snapshots"] == 1  # ts older than 2y: 3y row
        assert db.query(MarketSnapshot).count() == 4  # dry-run deletes nothing
        done = purge(db, now=now)
        # Backstop (list order) consumes the 3y row; tiers take the rest.
        assert done["counts"]["market_snapshots"] == 1
        assert done["counts"]["snapshots_raw"] == 1
        assert done["counts"]["snapshots_compressed"] == 1
        remaining = db.query(MarketSnapshot).all()
        assert len(remaining) == 1
        assert remaining[0].encoding == "gzip+json"
    finally:
        db.close()


def test_retention_env_override_for_tiers(monkeypatch):
    from backend.observability.retention import get_retention_days

    monkeypatch.setenv("RETENTION_SNAPSHOTS_RAW_DAYS", "5")
    assert get_retention_days()["snapshots_raw"] == 5
    assert get_retention_days({"snapshots_compressed": 7})["snapshots_compressed"] == 7


# --- workers ----------------------------------------------------------------


def test_capture_snapshot_job_with_fake_market_and_db():
    from backend.workers.jobs import capture_snapshot

    store.clear_memory()
    bars = _walk_bars(40)
    out = capture_snapshot("TEST", db=None, market=_FakeMarket(bars))
    assert out["ok"] is True and out["persisted"] is False
    assert out["n_bars"] == 40 and out["interval_min"] == 60
    assert out["size_reduction_pct"] > 0
    assert out["provenance"]["versions"]["worker"]
    db = _session()
    try:
        out2 = capture_snapshot("TEST", db=db, market=_FakeMarket(bars))
        assert out2["ok"] is True and out2["persisted"] is True
        intraday = capture_snapshot("TEST", timeframe="15m", db=None,
                                    market=_FakeMarket(bars))
        assert intraday["interval_min"] == 15
    finally:
        db.close()


def test_capture_snapshot_no_bars_is_ok_false():
    from backend.workers.jobs import capture_snapshot

    class _Empty:
        def get_bars(self, *a, **k):
            return {"bars": [], "provenance": {}}

    out = capture_snapshot("TEST", db=None, market=_Empty())
    assert out["ok"] is False and "bars" in out["errors"]


def test_score_forecasts_job_stub_and_cadence(monkeypatch):
    from backend.workers.jobs import (
        score_forecasts,
        snapshot_interval_min,
    )

    out = score_forecasts(db=None)
    assert out["ok"] is True and out["scored"] == 0 and out["stub"] is True
    assert snapshot_interval_min("1d") == 60
    assert snapshot_interval_min("15m") == 15
    monkeypatch.setenv("SNAPSHOT_INTERVAL_DAILY_MIN", "30")
    assert snapshot_interval_min("1d") == 30


# --- cron routes: 4 new distinct paths, 6 existing preserved ----------------


def test_cron_routes_preserved_and_extended():
    from backend.api.cron import router

    by_path: dict[str, set[str]] = {}
    for route in router.routes:
        methods = set(getattr(route, "methods", set()) or set())
        by_path.setdefault(getattr(route, "path", ""), set()).update(methods)
    for old in ("/api/cron/ingest", "/api/cron/calibrate", "/api/cron/evaluate"):
        assert {"GET", "POST"} <= by_path.get(old, set()), f"{old} regressed"
    for new in ("/api/cron/snapshot", "/api/cron/score"):
        assert {"GET", "POST"} <= by_path.get(new, set()), f"{new} missing"


def test_cron_snapshot_and_score_runners_hermetic(monkeypatch):
    import backend.api.cron as cron
    import backend.api.deps as deps
    import backend.db.session as sess

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(sess, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(sess, "get_session_factory", lambda *a, **k: factory)
    monkeypatch.setattr(deps, "get_market_service",
                        lambda: _FakeMarket(_walk_bars(35)))

    snap = cron._run_snapshot(["HERM"], "1d")
    assert snap["ok"] is True and snap["snapshots"]["HERM"]["n_bars"] == 35
    assert snap["snapshots"]["HERM"]["persisted"] is True

    db = factory()
    try:
        _seed_scored_world(db, symbol="HERM")
        db.commit()
    finally:
        db.close()
    scored = cron._run_score(["HERM"])
    assert scored["scored"] == 1 and not scored["errors"]
