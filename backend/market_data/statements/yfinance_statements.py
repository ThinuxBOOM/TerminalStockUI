"""yfinance statement provider (free global fundamentals fallback).

Annual income statement / balance sheet / cash flow via the ``yfinance``
package (already a hard dependency; no key). Coverage is global — this is
the only free leg for SSE (XSHG) and Euronext (XPAR/XAMS/XBRU), where SEC
EDGAR has no filers — plus the US fallback when EDGAR is unreachable.
Yahoo's non-US statements are thinner (missing concepts stay missing and
surface as honest "unavailable" downstream, never zero-filled).

Same resilience shape as the quote providers: circuit breaker,
token-bucket limiter, tenacity retry on transient failures, health hook.
Fail-closed: any outage/empty frame raises :class:`ProviderError` — never
stub numbers. Lazy ``yfinance`` import keeps the module import-safe.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed

from backend.market_data.providers.base import CircuitBreaker, ProviderError, RateLimiter

NAME = "yfinance-statements"
CACHE_TTL_S = 24 * 3600
CACHE_MAX_ENTRIES = 256

#: Canonical field -> (statement group, Yahoo row-label aliases in order).
#: First alias present with finite annual values wins for a column.
_LABELS: dict[str, tuple[str, tuple[str, ...]]] = {
    "revenue": ("is", ("Total Revenue", "TotalRevenue", "Revenue")),
    "gross_profit": ("is", ("Gross Profit", "GrossProfit")),
    "cogs": ("is", ("Cost Of Revenue", "CostOfRevenue", "Cost Of Goods Sold")),
    "sga_expense": ("is", ("Selling General And Administration",
                            "SellingGeneralAndAdministration")),
    "operating_income": ("is", ("Operating Income", "OperatingIncome")),
    "net_income": ("is", ("Net Income", "NetIncome")),
    "ebit": ("is", ("EBIT", "Ebit")),
    "interest_expense": ("is", ("Interest Expense", "InterestExpense")),
    "tax_expense": ("is", ("Tax Provision", "TaxProvision",
                            "Income Tax Expense")),
    "pretax_income": ("is", ("Pretax Income", "Earnings Before Tax",
                              "Income Before Tax")),
    "total_assets": ("bs", ("Total Assets", "TotalAssets")),
    "current_assets": ("bs", ("Current Assets", "CurrentAssets")),
    "total_liabilities": ("bs", ("Total Liabilities", "TotalLiabilities",
                                 "Total Liab")),
    "current_liabilities": ("bs", ("Current Liabilities",
                                   "CurrentLiabilities")),
    "total_equity": ("bs", ("Total Equity Gross Minority Interest",
                            "Stockholders Equity", "Total Equity",
                            "TotalEquityGrossMinorityInterest")),
    "retained_earnings": ("bs", ("Retained Earnings", "RetainedEarnings")),
    "receivables": ("bs", ("Accounts Receivable", "AccountsReceivable",
                           "Receivables")),
    "ppe_net": ("bs", ("Net PPE", "Property Plant Equipment Net",
                       "NetPPE")),
    "depreciation": ("cf", ("Depreciation And Amortization",
                            "DepreciationDepletionAmortization")),
    "long_term_debt": ("bs", ("Long Term Debt", "LongTermDebt")),
    "short_term_debt": ("bs", ("Current Debt", "Short Term Debt",
                               "CurrentDebt")),
    "operating_cash_flow": ("cf", ("Operating Cash Flow",
                                   "Total Cash From Operating Activities",
                                   "OperatingCashFlow")),
    "capital_expenditure": ("cf", ("Capital Expenditure",
                                  "CapitalExpenditures")),
    "shares_outstanding": ("info", ("sharesOutstanding", "impliedSharesOutstanding")),
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


class YFinanceStatementsProvider:
    """Live Yahoo annual statements (no key). Fail-closed, never stubbed."""

    name = NAME

    def __init__(
        self,
        *,
        breaker: CircuitBreaker | None = None,
        limiter: RateLimiter | None = None,
        stub_mode: bool = False,
        on_call: object | None = None,
    ) -> None:
        self.breaker = breaker or CircuitBreaker()
        self.limiter = limiter or RateLimiter(rate_per_sec=2.0, burst=4)
        self.stub_mode = stub_mode
        self._on_call = on_call
        self._cache: dict[str, tuple[float, tuple[dict, dict]]] = {}

    # -- internals ------------------------------------------------------
    def _emit(self, latency_ms: float, ok: bool, *, error=None, status_code=None) -> None:
        from backend.market_data.providers.base import emit_health as _emit_health

        _emit_health(self._on_call, self.name, latency_ms, ok,
                     error=error, status_code=status_code)

    def _cache_get(self, key: str) -> tuple[dict, dict] | None:
        try:
            entry = self._cache.get(key)
        except Exception:
            return None
        if not entry:
            return None
        expires_at, payload = entry
        if time.monotonic() >= expires_at:
            try:
                self._cache.pop(key, None)
            except Exception:
                pass
            return None
        return payload

    def _cache_put(self, key: str, payload: tuple[dict, dict]) -> None:
        try:
            self._cache[key] = (time.monotonic() + CACHE_TTL_S, payload)
            while len(self._cache) > CACHE_MAX_ENTRIES:
                self._cache.pop(next(iter(self._cache)), None)
        except Exception:
            pass

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_fixed(1),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
    def _fetch_frames(self, symbol: str) -> tuple[dict, dict, dict, dict]:
        try:  # lazy: offline/test envs stay import-safe
            import yfinance as yf
        except Exception as exc:
            raise ProviderError(NAME, "yfinance package unavailable") from exc
        if yf is None:
            raise ProviderError(NAME, "yfinance package unavailable")
        try:
            ticker = yf.Ticker(symbol)
            inc = ticker.financials
            bal = ticker.balance_sheet
            cfs = ticker.cashflow
            try:
                info = dict(ticker.info or {})
            except Exception:
                info = {}
        except ProviderError:
            raise
        except Exception as exc:  # network / parse failure
            raise ProviderError(NAME, f"{type(exc).__name__}: {exc}") from exc
        return inc, bal, cfs, info

    @staticmethod
    def _frame_to_cols(frame: object) -> tuple[list[str], dict[str, dict[str, float]]]:
        """Normalize one Yahoo statement frame to ({col: {label: value}}).

        Columns sorted newest-first (ISO date keys). Non-finite cells are
        dropped (missing stays missing downstream). Never raises.
        """
        try:
            import pandas as pd  # type: ignore[import-not-found]
        except Exception:
            return [], {}
        try:
            if frame is None or getattr(frame, "empty", True):
                return [], {}
            df = pd.DataFrame(frame)
            if df.empty or len(df.columns) == 0:
                return [], {}
            cols = sorted(
                (str(c)[:10] for c in df.columns),
                reverse=True,
            )
            # Map ISO col back to the original column label (duplicates: first).
            by_iso: dict[str, object] = {}
            for c in df.columns:
                iso = str(c)[:10]
                if iso not in by_iso:
                    by_iso[iso] = c
            out: dict[str, dict[str, float]] = {}
            for iso in cols:
                col = by_iso[iso]
                cells: dict[str, float] = {}
                try:
                    series = df[col]
                except (KeyError, IndexError):
                    continue
                for label, value in series.items():
                    number = _finite(value)
                    if number is not None:
                        cells[str(label).strip()] = number
                out[iso] = cells
            return cols, out
        except Exception:
            return [], {}

    # -- public ---------------------------------------------------------
    def get_annual_statements(self, symbol: str) -> tuple[dict, dict]:
        """Annual current+prior statement mapping + info envelope.

        Returns ``(mapping, info)`` with canonical raw fields (same contract
        as :class:`SecEdgarProvider`; derived ratios live in the resolver).
        Raises :class:`ProviderError` on any failure (never stub numbers).
        """
        upper = (symbol or "").strip().upper()
        if not upper:
            raise ProviderError(NAME, "empty symbol", retryable=False)
        hit = self._cache_get(upper)
        if hit is not None:
            return hit
        if self.stub_mode:
            self._emit(0.0, False, error="stub mode: statements never stubbed")
            raise ProviderError(NAME, "stub mode: no live statements")
        if not self.breaker.allow_request():
            raise ProviderError(NAME, "circuit open (breaker)")
        self.limiter.acquire()  # counted, never blocks
        started = time.perf_counter()
        try:
            inc, bal, cfs, info = self._fetch_frames(upper)
        except ProviderError as exc:
            self.breaker.record_failure()
            self._emit((time.perf_counter() - started) * 1000, False,
                       error=f"{type(exc).__name__}: {exc}")
            raise
        groups = {
            "is": self._frame_to_cols(inc)[1],
            "bs": self._frame_to_cols(bal)[1],
            "cf": self._frame_to_cols(cfs)[1],
        }
        # Anchor ends on the income statement (revenue's periods); other
        # groups prefer the same ends so margins never mix fiscal years.
        is_periods = sorted(groups["is"], reverse=True)
        if not is_periods:
            self.breaker.record_failure()
            self._emit((time.perf_counter() - started) * 1000, False,
                       error="empty income statement")
            raise ProviderError(NAME, f"no annual statements for {upper!r}")
        current, prior = is_periods[0], (is_periods[1] if len(is_periods) > 1 else None)
        mapping: dict[str, float] = {}
        for field, (group, aliases) in _LABELS.items():
            try:
                if group == "info":
                    for alias in aliases:
                        number = _finite((info or {}).get(alias))
                        if number is not None:
                            mapping[field] = number
                            break
                    continue
                table = groups.get(group) or {}
                for alias in aliases:
                    hit_cur = table.get(current, {}).get(alias)
                    if hit_cur is None:
                        continue
                    mapping[field] = float(hit_cur)
                    if prior is not None:
                        hit_prior = table.get(prior, {}).get(alias)
                        if hit_prior is not None:
                            mapping[f"{field}_prior"] = float(hit_prior)
                    break
            except Exception:
                continue
        if "revenue" not in mapping:
            self.breaker.record_failure()
            self._emit((time.perf_counter() - started) * 1000, False,
                       error="no annual revenue row")
            raise ProviderError(NAME, f"no annual revenue for {upper!r}")
        self.breaker.record_success()
        self._emit((time.perf_counter() - started) * 1000, True)
        try:
            currency = str((info or {}).get("currency") or "").strip().upper() or None
        except Exception:
            currency = None
        result = (
            mapping,
            {
                "source": NAME,
                "currency": currency,
                "fiscal_ends": [current] + ([prior] if prior else []),
                "filed_as_of": None,  # Yahoo gives period ends, not filing dates
                "forms": ["annual"],
            },
        )
        self._cache_put(upper, result)
        return result


__all__ = ["YFinanceStatementsProvider", "NAME"]
