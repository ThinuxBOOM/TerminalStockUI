"""Free statement vendors (financial-statement feed for analytics).

Free-first, no API keys (fits the AI-disabled path and the fail-closed
contract):

- ``sec_edgar`` — SEC EDGAR XBRL ``companyfacts`` (authoritative, US-listed
  incl. 20-F/40-F foreign filers, updated <1min after filings). No key;
  fair use is 10 req/s + a mandatory User-Agent contact string.
- ``yfinance_statements`` — Yahoo annual income/balance/cash-flow (global
  incl. SSE/Euronext where EDGAR has no coverage, plus US fallback).
  Already a hard dependency; no key.

``resolver.get_statements`` routes by market (US -> SEC then yfinance;
SSE/Euronext -> yfinance only), derives ratio-ready fields
(working_capital, margins, turnover, FCF, market values from the quote),
and returns ``(mapping, info)``. Total upstream failure yields
``({}, info-with-source-None)`` — callers keep today's honest
"unavailable" behavior, never fabricated numbers.
"""

from backend.market_data.statements.resolver import get_statements
from backend.market_data.statements.sec_edgar import SecEdgarProvider
from backend.market_data.statements.yfinance_statements import YFinanceStatementsProvider

__all__ = [
    "SecEdgarProvider",
    "YFinanceStatementsProvider",
    "get_statements",
]
