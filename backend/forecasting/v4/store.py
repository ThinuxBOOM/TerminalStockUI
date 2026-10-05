"""Persistence for v4: model bundles, daily cross-sections and scores.

The active bundle is cached in-process and re-checked every few minutes, so
a weekly retrain is picked up without a restart. When the database has no
bundle yet (fresh install) the committed ``default_bundle.json`` is used, so
forecasts work before the first training run.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select, update

from .model import ENGINE, Bundle, CrossSection

log = logging.getLogger(__name__)

DEFAULT_BUNDLE = Path(__file__).with_name("default_bundle.json")
_RECHECK_SECONDS = 300

_lock = threading.Lock()
_cache: dict = {"bundle": None, "checked": 0.0, "cs": None, "cs_checked": 0.0}


def _session():
    from backend.db.session import ensure_schema, get_session_factory

    ensure_schema()
    return get_session_factory()()


def save_bundle(bundle: Bundle, *, activate: bool = True) -> None:
    from backend.db.models import ModelArtifact

    import json

    db = _session()
    try:
        if activate:
            db.execute(update(ModelArtifact).where(ModelArtifact.engine == ENGINE).values(active=False))
        db.add(ModelArtifact(engine=ENGINE, version=bundle.version, bundle=json.loads(bundle.to_json()), active=activate))
        db.commit()
    finally:
        db.close()
    reset_cache()


def _load_active_from_db() -> Bundle | None:
    from backend.db.models import ModelArtifact

    db = _session()
    try:
        row = db.execute(
            select(ModelArtifact).where(ModelArtifact.engine == ENGINE, ModelArtifact.active.is_(True))
            .order_by(ModelArtifact.created_at.desc()).limit(1)
        ).scalar_one_or_none()
        return Bundle.from_json(row.bundle) if row is not None else None
    finally:
        db.close()


def load_default() -> Bundle | None:
    try:
        return Bundle.from_json(DEFAULT_BUNDLE.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError) as exc:
        log.warning("default v4 bundle unavailable: %s", exc)
        return None


def get_bundle() -> Bundle | None:
    """Active bundle (DB first, committed default otherwise); cached."""
    now = time.monotonic()
    with _lock:
        if _cache["bundle"] is not None and now - _cache["checked"] < _RECHECK_SECONDS:
            return _cache["bundle"]
    try:
        bundle = _load_active_from_db()
    except Exception as exc:  # DB down: keep serving what we have
        log.warning("loading v4 bundle from DB failed: %s", exc)
        bundle = None
    bundle = bundle or _cache["bundle"] or load_default()
    with _lock:
        _cache["bundle"], _cache["checked"] = bundle, now
    return bundle


def reset_cache() -> None:
    with _lock:
        _cache.update(bundle=None, checked=0.0, cs=None, cs_checked=0.0)


# --- cross-sections ----------------------------------------------------------

def save_cross_section(cs: CrossSection, model_version: str) -> None:
    from dataclasses import asdict

    from backend.db.models import CrossSectionRow

    db = _session()
    try:
        day = date.fromisoformat(cs.as_of)
        row = db.get(CrossSectionRow, day)
        if row is None:
            db.add(CrossSectionRow(as_of=day, model_version=model_version, data=asdict(cs)))
        else:
            row.model_version, row.data = model_version, asdict(cs)
        db.commit()
    finally:
        db.close()
    with _lock:
        _cache["cs"], _cache["cs_checked"] = cs, time.monotonic()


def latest_cross_section(bundle: Bundle | None) -> CrossSection | None:
    """Newest daily cross-section, else the one shipped with the bundle."""
    now = time.monotonic()
    with _lock:
        if _cache["cs"] is not None and now - _cache["cs_checked"] < _RECHECK_SECONDS:
            return _cache["cs"]
    cs = None
    try:
        from backend.db.models import CrossSectionRow

        db = _session()
        try:
            row = db.execute(select(CrossSectionRow).order_by(CrossSectionRow.as_of.desc()).limit(1)).scalar_one_or_none()
            if row is not None:
                cs = CrossSection(**row.data)
        finally:
            db.close()
    except Exception as exc:
        log.warning("loading cross-section failed: %s", exc)
    if cs is None and bundle is not None:
        cs = bundle.cross_section
    elif cs is not None and bundle is not None and bundle.cross_section is not None and bundle.cross_section.as_of > cs.as_of:
        cs = bundle.cross_section
    with _lock:
        _cache["cs"], _cache["cs_checked"] = cs, now
    return cs


# --- daily scores --------------------------------------------------------------

SCORE_COLUMNS = ("p_up", "p_out", "out_rank", "sigma", "q10", "q50", "q90", "drawdown_prob")


def save_scores(rows: list[dict]) -> int:
    """Upsert (symbol, horizon) rows from :func:`serve.score_universe`."""
    from backend.db.models import ForecastScore

    db = _session()
    try:
        for r in rows:
            key = (r["symbol"], int(r["horizon_days"]))
            row = db.get(ForecastScore, key)
            values = {
                "exchange_mic": r["exchange_mic"], "as_of": date.fromisoformat(r["as_of"]),
                "model_version": r["model_version"], "last_close": r.get("last_close"),
                "vol_regime": r.get("vol_regime"), "payload": r.get("payload") or {},
                "updated_at": datetime.now(timezone.utc),
                **{c: r.get(c) for c in SCORE_COLUMNS},
            }
            if row is None:
                db.add(ForecastScore(symbol=key[0], horizon_days=key[1], **values))
            else:
                for k, v in values.items():
                    setattr(row, k, v)
        db.commit()
        return len(rows)
    finally:
        db.close()


def read_scores(horizon: int, *, mics: list[str] | None = None, limit: int = 600) -> list[dict]:
    from backend.db.models import ForecastScore

    db = _session()
    try:
        q = select(ForecastScore).where(ForecastScore.horizon_days == int(horizon))
        if mics:
            q = q.where(ForecastScore.exchange_mic.in_(mics))
        rows = db.execute(q.order_by(ForecastScore.out_rank.desc()).limit(limit)).scalars().all()
        out = []
        for r in rows:
            d = {c: (float(getattr(r, c)) if getattr(r, c) is not None else None) for c in SCORE_COLUMNS}
            d.update(symbol=r.symbol, horizon_days=r.horizon_days, exchange_mic=r.exchange_mic,
                     as_of=r.as_of.isoformat() if r.as_of else None, model_version=r.model_version,
                     last_close=float(r.last_close) if r.last_close is not None else None,
                     vol_regime=r.vol_regime, payload=r.payload or {},
                     updated_at=r.updated_at.isoformat() if r.updated_at else None)
            out.append(d)
        return out
    finally:
        db.close()
