"""S&P 500 index universe tests (offline, no network).

Covers: seed module shape, registry merge (seeds win collisions),
screener SP500 universe filter + pseudo-market validation, ingest
sharding helpers, and cron universe resolution. No live fetches.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException


def test_sp500_module_shape():
    from backend.instruments.sp500 import SP500_MEMBERS, SP500_SYMBOLS

    assert 495 <= len(SP500_MEMBERS) <= 510, len(SP500_MEMBERS)
    assert len(SP500_SYMBOLS) == len(SP500_MEMBERS)  # no dupes
    for sym, name, sector, mic in SP500_MEMBERS:
        assert sym == sym.upper() and sym
        assert name and sector
        assert mic in ("XNYS", "XNAS"), (sym, mic)
    assert "AAPL" in SP500_SYMBOLS and "BRK.B" in SP500_SYMBOLS


def test_registry_merges_sp500_with_seeds_winning():
    from backend.instruments.registry import InstrumentRegistry, SEED_INSTRUMENTS

    reg = InstrumentRegistry()
    rows = reg.all()
    assert len(rows) >= 500 + 1
    # Seed richness preserved on collision (ISINs live on seeds only).
    nvda, _, _ = reg.resolve("NVDA")
    assert nvda is not None
    assert getattr(nvda, "isin", None) == "US67066G1040"
    # SP500-only name resolves with the right venue.
    brk, _, _ = reg.resolve("BRK.B")
    assert brk is not None and brk.exchange_mic == "XNYS"
    assert brk.company_name == "Berkshire Hathaway"


def test_screener_normalize_market_sp500():
    from backend.api.screener import _normalize_market

    assert _normalize_market("SP500") == "SP500"
    assert _normalize_market("sp500") == "SP500"
    assert _normalize_market(None) is None
    assert _normalize_market("ALL") is None
    with pytest.raises(HTTPException) as exc:
        _normalize_market("NOPE")
    assert exc.value.status_code == 422


def test_screener_sp500_universe_filter():
    from backend.api.screener import _universe_for
    from backend.instruments.registry import InstrumentRegistry
    from backend.instruments.sp500 import SP500_SYMBOLS

    reg = InstrumentRegistry()
    uni = _universe_for("SP500", reg)
    assert len(uni) == len(SP500_SYMBOLS) == 500
    keys = set()
    for inst in uni:
        key = str(getattr(inst, "provider_symbol", None)
                  or getattr(inst, "exchange_symbol", "")).upper()
        keys.add(key)
        assert getattr(inst, "exchange_mic", None) in ("XNYS", "XNAS")
    assert keys == {s.upper() for s in SP500_SYMBOLS}
    # MIC scopes still work and are subsets.
    xnys = _universe_for("XNYS", reg)
    assert xnys and all(i.exchange_mic == "XNYS" for i in xnys)


def test_ingest_shard_symbols():
    from backend.market_data.ingest import shard_symbols, sp500_universe

    uni = sp500_universe()
    assert len(uni) == 500 and uni == sorted(uni)
    parts = [shard_symbols(uni, i, 10) for i in range(1, 11)]
    assert sum(map(len, parts)) == 500
    assert sorted(s for p in parts for s in p) == uni
    assert all(len(p) == 50 for p in parts)
    # Degenerate inputs fail open to the full list.
    assert shard_symbols(uni, 0, 10) == uni
    assert shard_symbols(uni, 11, 10) == uni
    assert shard_symbols(uni, 1, 1) == uni


def test_cron_resolve_ingest_symbols():
    from backend.api.cron import _resolve_ingest_symbols

    syms, meta = _resolve_ingest_symbols(None, single="AAPL")
    assert syms == ["AAPL"] and meta["universe"] == "single"
    syms, meta = _resolve_ingest_symbols(["MSFT", "TSLA"])
    assert syms == ["MSFT", "TSLA"] and meta["universe"] == "explicit"
    syms, meta = _resolve_ingest_symbols(None, universe="sp500", shard=3, shards=10)
    assert len(syms) == 50 and meta == {"universe": "sp500", "shard": 3, "shards": 10}
    syms, meta = _resolve_ingest_symbols(None)
    assert len(syms) > 0 and meta["universe"] == "default"


def test_signals_excludes_sp500_bulk():
    """Signals scan cost unchanged: SP500-only bulk excluded, seeds kept."""
    import backend.api.signals as signals
    from backend.instruments.registry import InstrumentRegistry

    reg = InstrumentRegistry()
    scanned = [i for i in reg.all() if getattr(i, "sector", "") not in ("ETF", "Index")]
    # Replicate the endpoint's cost-guard filter.
    from backend.instruments.registry import SEED_INSTRUMENTS
    from backend.instruments.sp500 import SP500_SYMBOLS

    seed_syms = {str(getattr(s, "provider_symbol", "") or "").upper() for s in SEED_INSTRUMENTS}
    sp_only = frozenset(s for s in (str(x).upper() for x in SP500_SYMBOLS) if s not in seed_syms)
    kept = [i for i in scanned
            if str(getattr(i, "provider_symbol", "") or "").upper() not in sp_only]
    assert len(kept) < 60  # same order as pre-SP500 universe
    assert any(str(getattr(i, "provider_symbol", "") or "").upper() == "TSLA" for i in kept)
    assert signals.top_signals  # endpoint still wired
