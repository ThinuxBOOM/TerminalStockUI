"""Free statement vendors: SEC EDGAR + yfinance + resolver (offline, canned).

No network: SEC HTTP is monkeypatched at ``_http_get_json``; yfinance
frames are real pandas DataFrames fed to the pure ``_frame_to_cols``
normalizer; resolver tests inject fake providers. Covers: ticker/CIK
resolution (+ seed fallback), concept priority fallback, annual-form +
duration gates, latest-filed-wins restatements, revenue-anchored period
alignment, derived fields, market routing (US -> SEC -> yfinance,
SSE/Euronext -> yfinance only), fail-closed empties, and the analytics
endpoint with live vs failed statements.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.analytics_api import router as analytics_router
from backend.market_data.providers.base import ProviderError
from backend.market_data.statements import concepts
from backend.market_data.statements.resolver import derive_fields, get_statements
from backend.market_data.statements.sec_edgar import SecEdgarProvider
from backend.market_data.statements.yfinance_statements import (
    YFinanceStatementsProvider,
)


def _fact(start, end, val, form="10-K", filed="2024-11-01", accn="0001"):
    return {"start": start, "end": end, "val": val, "accn": accn,
            "fy": 2024, "fp": "FY", "form": form, "filed": filed}


def _canned_companyfacts() -> dict:
    return {
        "cik": "0000320193",
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                # Primary revenue tag absent on purpose -> Revenues fallback.
                "Revenues": {"units": {"USD": [
                    _fact("2023-10-01", "2024-09-28", 391035000000.0),
                    _fact("2022-10-01", "2023-09-30", 383285000000.0,
                          filed="2023-11-03"),
                    # Restatement of the same end, older filing -> must lose.
                    _fact("2022-10-01", "2023-09-30", 380000000000.0,
                          filed="2022-11-01", accn="0000"),
                    # Quarter -> must be ignored (92-day duration).
                    _fact("2024-06-30", "2024-09-28", 94930000000.0,
                          form="10-Q"),
                ]}},
                "GrossProfit": {"units": {"USD": [
                    _fact("2023-10-01", "2024-09-28", 183000000000.0),
                    _fact("2022-10-01", "2023-09-30", 170000000000.0,
                          filed="2023-11-03"),
                ]}},
                "NetIncomeLoss": {"units": {"USD": [
                    _fact("2023-10-01", "2024-09-28", 93736000000.0),
                    _fact("2022-10-01", "2023-09-30", 96995000000.0,
                          filed="2023-11-03"),
                ]}},
                "Assets": {"units": {"USD": [
                    _fact("2024-09-28", "2024-09-28", 352755000000.0),
                    _fact("2023-09-30", "2023-09-30", 352583000000.0,
                          filed="2023-11-03"),
                ]}},
                "StockholdersEquity": {"units": {"USD": [
                    _fact("2024-09-28", "2024-09-28", 57468000000.0),
                    _fact("2023-09-30", "2023-09-30", 62146000000.0,
                          filed="2023-11-03"),
                ]}},
                "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
                    _fact("2023-10-01", "2024-09-28", 118254000000.0),
                    _fact("2022-10-01", "2023-09-30", 110543000000.0,
                          filed="2023-11-03"),
                ]}},
                "CommonStockSharesOutstanding": {"units": {"shares": [
                    _fact("2024-09-28", "2024-09-28", 15204000000.0),
                    _fact("2023-09-30", "2023-09-30", 15550000000.0,
                          filed="2023-11-03"),
                ]}},
            }
        },
    }


def _canned_ticker_map() -> dict:
    return {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft"}}


# --- concepts ---------------------------------------------------------------

def test_revenue_falls_back_to_revenues_concept():
    facts = _canned_companyfacts()
    series, unit = concepts.pick_metric_series(facts, "revenue")
    assert unit == "USD"
    assert [end for end, _, _ in series] == ["2024-09-28", "2023-09-30"]
    assert series[0][1] == pytest.approx(391035000000.0)
    # Restatement: newest filing wins for the 2023 end.
    assert series[1][1] == pytest.approx(383285000000.0)


def test_quarterly_facts_excluded_from_annual_series():
    facts = _canned_companyfacts()
    series, _ = concepts.pick_metric_series(facts, "revenue")
    assert all(v != pytest.approx(94930000000.0) for _, v, _ in series)
    assert len(series) == 2


def test_prefer_ends_aligns_periods():
    facts = _canned_companyfacts()
    ends, _ = concepts.anchor_ends(facts)
    assert ends == ["2024-09-28", "2023-09-30"]
    gp, _ = concepts.pick_metric_series(facts, "gross_profit", prefer_ends=ends)
    assert [e for e, _, _ in gp] == ends
    # Unknown metric -> empty, never raises.
    assert concepts.pick_metric_series(facts, "nope")[0] == []
    assert concepts.pick_metric_series({}, "revenue")[0] == []
    assert concepts.anchor_ends({})[0] == []


def _facts_for(concept: str, fact_list: list) -> dict:
    return {"facts": {"us-gaap": {concept: {"units": {"USD": fact_list}}}}}


def test_instant_vs_flow_duration_gate():
    qtr = [_fact("2024-06-30", "2024-09-28", 1.0, form="10-Q")]
    assert concepts.pick_metric_series(
        _facts_for("Assets", qtr), "total_assets")[0] == []
    # 10-Q instant rejected (annual forms only)
    annual = [_fact("2024-09-28", "2024-09-28", 1.0)]
    assert concepts.pick_metric_series(
        _facts_for("Assets", annual), "total_assets")[0] != []


# --- SEC provider -----------------------------------------------------------

class _FakeSecHttp:
    def __init__(self, tickers, facts):
        self.tickers = tickers
        self.facts = facts
        self.urls: list[str] = []

    def __call__(self, url: str) -> dict:
        self.urls.append(url)
        if "company_tickers" in url:
            return self.tickers
        if "companyfacts" in url:
            return self.facts
        raise AssertionError(f"unexpected URL {url}")


def test_sec_resolve_cik_and_seed_fallback(monkeypatch):
    prov = SecEdgarProvider()
    fake = _FakeSecHttp(_canned_ticker_map(), _canned_companyfacts())
    monkeypatch.setattr(prov, "_http_get_json", fake)
    assert prov.resolve_cik("aapl") == 320193
    assert prov.resolve_cik("MSFT") == 789019
    with pytest.raises(ProviderError):
        prov.resolve_cik("ZZZ_NOPE_123")
    with pytest.raises(ProviderError):
        prov.resolve_cik("   ")
    # Map fetch down -> seed map still resolves the registry universe.
    prov2 = SecEdgarProvider()

    def _boom(url: str) -> dict:
        raise ProviderError("sec-edgar", "network down")

    monkeypatch.setattr(prov2, "_http_get_json", _boom)
    assert prov2.resolve_cik("AAPL") == 320193
    with pytest.raises(ProviderError):
        prov2.resolve_cik("UNKNOWN_XYZ")


def test_sec_annual_statements_mapping_and_info(monkeypatch):
    prov = SecEdgarProvider()
    fake = _FakeSecHttp(_canned_ticker_map(), _canned_companyfacts())
    monkeypatch.setattr(prov, "_http_get_json", fake)
    mapping, info = prov.get_annual_statements("AAPL")
    assert mapping["revenue"] == pytest.approx(391035000000.0)
    assert mapping["revenue_prior"] == pytest.approx(383285000000.0)
    assert mapping["gross_profit"] == pytest.approx(183000000000.0)
    assert mapping["total_assets"] == pytest.approx(352755000000.0)
    assert mapping["shares_outstanding"] == pytest.approx(15204000000.0)
    assert info["source"] == "sec-edgar"
    assert info["cik"] == 320193
    assert info["currency"] == "USD"
    assert info["fiscal_ends"] == ["2024-09-28", "2023-09-30"]
    assert info["filed_as_of"] == "2024-11-01"
    # Second call served from the 24h facts cache (no new facts URL hit).
    n_facts_hits = sum(1 for u in fake.urls if "companyfacts" in u)
    mapping2, _ = prov.get_annual_statements("AAPL")
    assert mapping2 == mapping
    assert sum(1 for u in fake.urls if "companyfacts" in u) == n_facts_hits


def test_sec_stub_mode_and_empty_raise_never_stub(monkeypatch):
    prov = SecEdgarProvider(stub_mode=True)
    with pytest.raises(ProviderError):
        prov.get_annual_statements("AAPL")


# --- yfinance normalizer ----------------------------------------------------

def _yahoo_frames():
    import pandas as pd

    idx = ["Total Revenue", "Gross Profit", "Net Income"]
    inc = pd.DataFrame(
        [[391035000000.0, 383285000000.0],
         [183000000000.0, 170000000000.0],
         [93736000000.0, float("nan")]],
        index=idx,
        columns=[pd.Timestamp("2024-09-28"), pd.Timestamp("2023-09-30")],
    )
    bal = pd.DataFrame(
        [[352755000000.0, 352583000000.0]],
        index=["Total Assets"],
        columns=[pd.Timestamp("2024-09-28"), pd.Timestamp("2023-09-30")],
    )
    cfs = pd.DataFrame(
        [[118254000000.0, 110543000000.0]],
        index=["Operating Cash Flow"],
        columns=[pd.Timestamp("2024-09-28"), pd.Timestamp("2023-09-30")],
    )
    return inc, bal, cfs


def test_yfinance_frame_normalizer_newest_first_and_finite_only():
    inc, bal, _ = _yahoo_frames()
    cols, table = YFinanceStatementsProvider._frame_to_cols(inc)
    assert cols == ["2024-09-28", "2023-09-30"]
    assert table["2024-09-28"]["Total Revenue"] == pytest.approx(391035000000.0)
    # NaN cell dropped (missing stays missing downstream).
    assert "Net Income" not in table["2023-09-30"]
    assert YFinanceStatementsProvider._frame_to_cols(None) == ([], {})
    assert YFinanceStatementsProvider._frame_to_cols("nope") == ([], {})


def test_yfinance_provider_mapping_with_stubbed_fetch(monkeypatch):
    prov = YFinanceStatementsProvider()
    inc, bal, cfs = _yahoo_frames()
    monkeypatch.setattr(prov, "_fetch_frames", lambda symbol: (inc, bal, cfs, {"currency": "USD"}))
    mapping, info = prov.get_annual_statements("AAPL")
    assert mapping["revenue"] == pytest.approx(391035000000.0)
    assert mapping["revenue_prior"] == pytest.approx(383285000000.0)
    assert "net_income_prior" not in mapping  # NaN stays missing
    assert info["source"] == "yfinance-statements"
    assert info["fiscal_ends"] == ["2024-09-28", "2023-09-30"]
    with pytest.raises(ProviderError):
        YFinanceStatementsProvider(stub_mode=True).get_annual_statements("AAPL")


# --- resolver ---------------------------------------------------------------

def _canned_raw() -> dict:
    return {
        "revenue": 391035000000.0, "revenue_prior": 383285000000.0,
        "gross_profit": 183000000000.0, "gross_profit_prior": 170000000000.0,
        "cogs": 208035000000.0,
        "operating_income": 123216000000.0, "net_income": 93736000000.0,
        "net_income_prior": 96995000000.0, "ebit": 123216000000.0,
        "interest_expense": 4000000000.0,
        "pretax_income": 113736000000.0, "tax_expense": 20000000000.0,
        "total_assets": 352755000000.0, "total_assets_prior": 352583000000.0,
        "current_assets": 143566000000.0, "current_assets_prior": 140000000000.0,
        "current_liabilities": 176392000000.0,
        "total_liabilities": 295287000000.0,
        "total_equity": 57468000000.0, "total_equity_prior": 62146000000.0,
        "retained_earnings": 10000000000.0,
        "receivables": 30000000000.0, "receivables_prior": 29000000000.0,
        "cogs_prior": 213000000000.0,
        "ppe_net": 45000000000.0, "ppe_net_prior": 44000000000.0,
        "depreciation": 11000000000.0, "depreciation_prior": 10500000000.0,
        "sga_expense": 26000000000.0, "sga_expense_prior": 25000000000.0,
        "long_term_debt": 95000000000.0, "long_term_debt_prior": 98000000000.0,
        "current_liabilities_prior": 170000000000.0,
        "operating_cash_flow": 118254000000.0,
        "capital_expenditure": 9500000000.0,
        "shares_outstanding": 15204000000.0,
        "shares_outstanding_prior": 15550000000.0,
    }


class _FakeProvider:
    def __init__(self, mapping=None, info=None, error=None):
        self.mapping = mapping
        self.info = info or {"source": "fake"}
        self.error = error
        self.calls: list[str] = []

    def get_annual_statements(self, symbol: str):
        self.calls.append(symbol)
        if self.error is not None:
            raise self.error
        return dict(self.mapping or {}), dict(self.info)


def test_derive_fields_ratios_fcf_tax_and_market_values():
    mapping = derive_fields(_canned_raw(), quote_price=232.50)
    assert mapping["working_capital"] == pytest.approx(143566000000.0 - 176392000000.0)
    assert mapping["current_ratio"] == pytest.approx(143566000000.0 / 176392000000.0)
    assert mapping["asset_turnover"] == pytest.approx(391035000000.0 / 352755000000.0)
    assert mapping["gross_margin"] == pytest.approx(183000000000.0 / 391035000000.0)
    assert mapping["gross_margin_prior"] == pytest.approx(
        170000000000.0 / 383285000000.0
    )
    assert mapping["base_fcf"] == pytest.approx(118254000000.0 - 9500000000.0)
    assert mapping["tax_rate"] == pytest.approx(20000000000.0 / 113736000000.0)
    assert mapping["market_value_equity"] == pytest.approx(232.50 * 15204000000.0)
    assert mapping["market_value_debt"] == pytest.approx(95000000000.0)
    # No quote -> no market cap (Altman stays unavailable, honestly).
    assert "market_value_equity" not in derive_fields(_canned_raw())
    # Negative pretax -> no tax rate (never a nonsense negative rate).
    bad = dict(_canned_raw(), pretax_income=-5.0)
    assert "tax_rate" not in derive_fields(bad, quote_price=10.0)
    # Empty / garbage never raises.
    assert derive_fields({}) == {}
    assert derive_fields(None) == {}


def test_resolver_us_prefers_sec_then_falls_back(monkeypatch):
    from backend import cache as cache_module

    cache_module.get_cache().delete("statements:AAPL:XNAS")
    sec = _FakeProvider(dict(_canned_raw()), {"source": "sec-edgar"})
    yf = _FakeProvider({"revenue": 1.0}, {"source": "yfinance-statements"})
    mapping, info = get_statements("AAPL", "XNAS", {"price": 232.50}, sec=sec, yf=yf)
    assert info["source"] == "sec-edgar"
    assert info["fallback_used"] is False
    assert yf.calls == []  # SEC won: no fallback call
    assert mapping["gross_margin"] == pytest.approx(183000000000.0 / 391035000000.0)

    cache_module.get_cache().delete("statements:AAPL:XNAS")
    sec_fail = _FakeProvider(error=ProviderError("sec-edgar", "network down"))
    mapping2, info2 = get_statements("AAPL", "XNAS", {"price": 232.50}, sec=sec_fail, yf=yf)
    assert info2["source"] == "yfinance-statements"
    assert info2["fallback_used"] is True
    assert "primary_unavailable" in info2
    cache_module.get_cache().delete("statements:AAPL:XNAS")


def test_resolver_non_us_never_touches_sec(monkeypatch):
    from backend import cache as cache_module

    for symbol, mic in (("600519.SS", "XSHG"), ("MC.PA", "XPAR"),
                        ("ASML.AS", "XAMS"), ("UCB.BR", "XBRU")):
        cache_module.get_cache().delete(f"statements:{symbol}:{mic}")

        def _must_not_run(_s, _sym=symbol):
            raise AssertionError(f"SEC must not be asked for {_sym}")

        sec = _FakeProvider(error=ProviderError("sec-edgar", "x"))
        sec.get_annual_statements = _must_not_run  # type: ignore[method-assign]
        yf = _FakeProvider(dict(_canned_raw()), {"source": "yfinance-statements"})
        mapping, info = get_statements(symbol, mic, {"price": 10.0}, sec=sec, yf=yf)
        assert info["source"] == "yfinance-statements"
        assert info["fallback_used"] is False
        assert mapping["revenue"] == pytest.approx(391035000000.0)
        cache_module.get_cache().delete(f"statements:{symbol}:{mic}")


def test_resolver_total_failure_returns_empty_never_raises():
    sec = _FakeProvider(error=ProviderError("sec-edgar", "down"))
    yf = _FakeProvider(error=ProviderError("yfinance-statements", "down"))
    mapping, info = get_statements("ZZZ_NOPE_123", None, None, sec=sec, yf=yf)
    assert mapping == {}
    assert info["source"] is None
    assert get_statements("   ")[0] == {}


# --- analytics endpoint -----------------------------------------------------

def _analytics_client() -> TestClient:
    from backend.tests.auth_helpers import inject_admin_auth

    # Start cold: the analytics endpoint cache is keyed by
    # symbol/indicators only, so per-test get_statements monkeypatches
    # would otherwise read a previous test's bundle (same convention as
    # screener/chart tests).
    try:
        from backend import cache as cache_module

        _c = cache_module.get_cache()
        _clear = getattr(_c, "clear", None)
        if callable(_clear):
            _clear()
    except Exception:
        pass
    app = FastAPI()
    app.include_router(analytics_router)
    inject_admin_auth(app)
    return TestClient(app)


def test_analytics_with_live_statements_computes_sections(monkeypatch):
    import backend.market_data.statements.resolver as resolver_module

    raw = _canned_raw()
    mapping = derive_fields(raw, quote_price=232.50)
    info = {"source": "sec-edgar", "currency": "USD",
            "fiscal_ends": ["2024-09-28", "2023-09-30"],
            "filed_as_of": "2024-11-01", "forms": ["10-K"],
            "fallback_used": False, "missing_fields": []}
    monkeypatch.setattr(resolver_module, "get_statements",
                        lambda *a, **k: (dict(mapping), dict(info)))
    body = _analytics_client().get("/api/analytics/AAPL").json()
    assert body["fundamentals"]["gross_margin"]["quality_flag"] == "ok"
    assert body["fundamentals"]["gross_margin"]["value"] == pytest.approx(
        183000000000.0 / 391035000000.0)
    assert body["quality"]["piotroski"]["quality_flag"] in ("ok", "degraded")
    assert body["quality"]["dupont"]["quality_flag"] in ("ok", "degraded")
    assert body["valuation"]["dcf_sensitivity"]["quality_flag"] == "ok"
    # WACC still unavailable: statements cannot supply market-implied costs.
    assert body["valuation"]["wacc"]["quality_flag"] == "unavailable"
    assert "cost_of_equity" in body["valuation"]["wacc"]["reason"]
    # Banner names the live feed; statements block carries provenance.
    assert "sec-edgar" in body["note"]
    assert body["statements"]["source"] == "sec-edgar"
    assert body["statements"]["fiscal_ends"] == ["2024-09-28", "2023-09-30"]


def test_analytics_statements_failure_keeps_unavailable_design(monkeypatch):
    import backend.market_data.statements.resolver as resolver_module

    def _fail(*a, **k):
        return {}, {"source": None, "reason": "network down"}

    monkeypatch.setattr(resolver_module, "get_statements", _fail)
    body = _analytics_client().get("/api/analytics/AAPL").json()
    assert body["fundamentals"]["gross_margin"]["quality_flag"] == "unavailable"
    assert body["quality"]["piotroski"]["quality_flag"] == "unavailable"
    assert body["valuation"]["wacc"]["quality_flag"] == "unavailable"
    assert "not wired" in body["note"]
