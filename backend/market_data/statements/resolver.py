"""Statement resolver: route by market, derive ratio-ready fields, cache.

Routing (free-first, no keys):

- US (XNYS/XNAS, bare tickers, unknown MIC): SEC EDGAR first (authoritative
  XBRL), yfinance statements on SEC failure.
- SSE (XSHG / ``.SS``) and Euronext (XPAR/XAMS/XBRU / ``.PA`` / ``.AS`` /
  ``.BR``): yfinance only — EDGAR has no Shanghai/Euronext filers and must
  never be asked for them (it would 404 on a missing CIK).

Derived fields (shared for both vendors, pure arithmetic on the raw
mapping; a missing input leaves the derived field missing — never
zero-filled):

- ``total_debt`` = short + long term debt (either leg alone suffices)
- ``working_capital`` = current assets − current liabilities
- ``current_ratio`` (+ ``_prior``), ``asset_turnover`` (+ ``_prior``),
  ``gross_margin`` (+ ``_prior``)
- ``base_fcf`` = operating cash flow − capital expenditure
- ``tax_rate`` = tax expense / pretax income (only when pretax > 0 and the
  rate lands in [0, 1]; effective-rate proxy, documented as such)
- ``market_value_equity`` = quote price × shares outstanding (needs the
  caller's quote; None without it — Altman then stays unavailable)
- ``market_value_debt`` = total debt book proxy (WACC still needs
  market-implied cost_of_equity/cost_of_debt, which statements cannot
  supply — WACC stays unavailable naming those two)

Fail-closed: total upstream failure returns ``({}, info)`` with
``info["source"] = None`` — callers keep today's honest "unavailable"
behavior. This function never raises for data-dependent reasons.
"""

from __future__ import annotations

import math
from typing import Any

STATEMENTS_CACHE_TTL_S = 24 * 3600

US_MICS = frozenset({"XNYS", "XNAS"})
NON_US_MICS = frozenset({"XSHG", "XPAR", "XAMS", "XBRU"})
NON_US_SUFFIXES = (".SS", ".PA", ".AS", ".BR")


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _is_non_us(symbol: str, mic: str | None) -> bool:
    try:
        mic_up = (mic or "").strip().upper()
    except Exception:
        mic_up = ""
    if mic_up in NON_US_MICS:
        return True
    if mic_up in US_MICS:
        return False
    try:
        upper = (symbol or "").strip().upper()
    except Exception:
        return False
    return upper.endswith(NON_US_SUFFIXES)


def derive_fields(
    raw: dict, *, quote_price: float | None = None
) -> dict[str, float]:
    """Raw vendor mapping -> raw + derived canonical mapping (pure).

    Never raises; missing inputs simply leave fields absent.
    """
    out: dict[str, float] = {}
    try:
        for key, value in (raw or {}).items():
            number = _num(value)
            if number is not None and isinstance(key, str):
                out[key] = number
    except Exception:
        pass

    def get(name: str) -> float | None:
        return out.get(name)

    try:
        std, ltd = get("short_term_debt"), get("long_term_debt")
        if std is not None and ltd is not None:
            out["total_debt"] = std + ltd
        elif ltd is not None:
            out["total_debt"] = ltd
        elif std is not None:
            out["total_debt"] = std
    except Exception:
        pass

    def _ratio_pair(num: str, den: str, out_name: str) -> None:
        for suffix, out_key in (("", out_name), ("_prior", f"{out_name}_prior")):
            try:
                n, d = get(f"{num}{suffix}"), get(f"{den}{suffix}")
                if n is not None and d is not None and d != 0:
                    out[out_key] = n / d
            except Exception:
                continue

    try:
        ca, cl = get("current_assets"), get("current_liabilities")
        if ca is not None and cl is not None:
            out["working_capital"] = ca - cl
    except Exception:
        pass
    _ratio_pair("current_assets", "current_liabilities", "current_ratio")
    _ratio_pair("revenue", "total_assets", "asset_turnover")
    _ratio_pair("gross_profit", "revenue", "gross_margin")
    try:
        ocf, capex = get("operating_cash_flow"), get("capital_expenditure")
        if ocf is not None and capex is not None:
            out["base_fcf"] = ocf - capex
    except Exception:
        pass
    try:
        tax, pretax = get("tax_expense"), get("pretax_income")
        if tax is not None and pretax is not None and pretax > 0:
            rate = tax / pretax
            if 0.0 <= rate <= 1.0:
                out["tax_rate"] = rate
    except Exception:
        pass
    try:
        price = _num(quote_price)
        shares = get("shares_outstanding")
        if price is not None and price > 0 and shares is not None and shares > 0:
            out["market_value_equity"] = price * shares
        debt = get("total_debt")
        if debt is not None and debt >= 0:
            out["market_value_debt"] = debt
    except Exception:
        pass
    return out


def _cache_key(symbol: str, mic: str | None) -> str:
    try:
        mic_part = (mic or "").strip().upper() or "NOMIC"
    except Exception:
        mic_part = "NOMIC"
    return f"statements:{(symbol or '').strip().upper()}:{mic_part}"


def get_statements(
    symbol: str,
    mic: str | None = None,
    quote: dict | None = None,
    *,
    sec: Any | None = None,
    yf: Any | None = None,
) -> tuple[dict[str, float], dict]:
    """Fetch + derive statements for ``symbol`` (never raises on data issues).

    Returns ``(mapping, info)``. ``info`` always carries ``source``
    (``"sec-edgar"`` / ``"yfinance-statements"`` / ``None`` when unavailable),
    ``currency``, ``fiscal_ends``, ``filed_as_of``, ``forms``,
    ``fallback_used`` and ``missing_fields`` (core raw fields absent).
    Vendor raw mappings are cached 24h (market values derive per call since
    the quote moves). ``sec``/``yf`` inject providers (tests); default
    instances are built lazily.
    """
    upper = (symbol or "").strip().upper()
    if not upper:
        return {}, {"source": None, "reason": "empty symbol"}
    try:
        price = None
        if isinstance(quote, dict):
            price = _num(quote.get("price"))
    except Exception:
        price = None

    raw: dict | None = None
    info: dict | None = None
    first_error: str | None = None
    non_us = _is_non_us(upper, mic)

    def _cached_or_fetch() -> tuple[dict | None, dict | None, str | None]:
        nonlocal first_error
        # Shared 24h cache for vendor RAW mappings (derived per call).
        try:
            from backend.cache import get_cache

            cached = get_cache().get(_cache_key(upper, mic))
            if isinstance(cached, dict) and isinstance(cached.get("raw"), dict):
                cached_info = cached.get("info")
                return (
                    dict(cached["raw"]),
                    dict(cached_info) if isinstance(cached_info, dict) else {},
                    None,
                )
        except Exception:
            pass
        raw_local: dict | None = None
        info_local: dict | None = None
        chain: list[tuple[str, Any]] = []
        if not non_us:
            # US first leg: SEC EDGAR (authoritative XBRL). Never attempted
            # for SSE/Euronext (no CIK coverage there by construction).
            try:
                from backend.market_data.statements.sec_edgar import SecEdgarProvider

                chain.append(("sec-edgar", sec or SecEdgarProvider()))
            except Exception as exc:
                first_error = f"sec-edgar unavailable: {type(exc).__name__}"
        try:
            from backend.market_data.statements.yfinance_statements import (
                YFinanceStatementsProvider,
            )

            # US: fallback reached only on SEC failure; non-US: the only leg.
            chain.append(("yfinance-statements", yf or YFinanceStatementsProvider()))
        except Exception as exc:
            if first_error is None:
                first_error = f"yfinance-statements unavailable: {type(exc).__name__}"
        for _name, provider in chain:
            try:
                raw_local, info_local = provider.get_annual_statements(upper)
                if isinstance(raw_local, dict) and raw_local.get("revenue") is not None:
                    break
                raw_local, info_local = None, None
            except Exception as exc:
                if first_error is None:
                    first_error = f"{_name}: {type(exc).__name__}: {exc}"[:220]
                raw_local, info_local = None, None
                continue
        if raw_local is None:
            return None, None, first_error
        try:
            from backend.cache import get_cache

            get_cache().set(
                _cache_key(upper, mic),
                {"raw": dict(raw_local), "info": dict(info_local or {})},
                ttl_s=STATEMENTS_CACHE_TTL_S,
            )
        except Exception:
            pass
        return raw_local, info_local or {}, None

    try:
        raw, info, _err = _cached_or_fetch()
    except Exception as exc:
        return {}, {"source": None, "reason": f"{type(exc).__name__}"[:160]}
    if not isinstance(raw, dict) or not raw:
        reason = first_error or "no statement vendor covers this symbol"
        return {}, {"source": None, "reason": reason[:220]}
    try:
        mapping = derive_fields(raw, quote_price=price)
    except Exception:
        mapping = {}
    out_info: dict = {"source": None}
    try:
        out_info.update(dict(info or {}))
    except Exception:
        pass
    try:
        out_info["fallback_used"] = bool(
            (out_info.get("source") == "yfinance-statements") and not non_us
        )
        if first_error and out_info["fallback_used"]:
            out_info["primary_unavailable"] = (first_error or "")[:220]
        core = ("revenue", "net_income", "total_assets", "total_equity",
                "operating_cash_flow")
        out_info["missing_fields"] = [k for k in core if mapping.get(k) is None]
    except Exception:
        pass
    return mapping, out_info


__all__ = ["derive_fields", "get_statements"]
