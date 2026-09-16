"""XBRL concept priority lists + annual fact picking (SEC EDGAR).

Two documented XBRL quirks drive this module:

1. Concept drift: the taxonomy has several tags meaning "revenue" and
   companies switch between them (e.g. NVIDIA
   ``RevenueFromContractWithCustomerExcludingAssessedTax`` -> ``Revenues``).
   Every canonical metric therefore tries a priority list of concepts and
   matches values on the same period end (never latest-of-each, which
   produced a famous 570% gross-margin bug).
2. Restatements: one period end can appear in several filings with different
   values. Latest ``filed`` date wins (comparative restatements are what a
   reader of the newest 10-K sees).

Annual discipline: only 10-K / 20-F / 40-F facts (plus amendments, which
carry the same form root) are eligible; flow facts must span 350-380 days
(a full fiscal year, not a quarter). Growth metrics are annual YoY.
"""

from __future__ import annotations

from datetime import datetime, timezone

#: Annual filing forms (amendments share the root: "10-K/A" startswith "10-K").
ANNUAL_FORMS = ("10-K", "20-F", "40-F")

#: Canonical metric -> XBRL concept priority (taxonomy, tag) lists.
#: us-gaap first; dei only for shares metadata. Order matters: first concept
#: with usable annual facts wins for a given period end.
CONCEPT_PRIORITY: dict[str, list[tuple[str, str]]] = {
    "revenue": [
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
        ("us-gaap", "SalesRevenueGoodsNet"),
    ],
    "gross_profit": [("us-gaap", "GrossProfit")],
    "cogs": [
        ("us-gaap", "CostOfGoodsAndServicesSold"),
        ("us-gaap", "CostOfGoodsSold"),
        ("us-gaap", "CostOfRevenue"),
        ("us-gaap", "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"),
    ],
    "sga_expense": [("us-gaap", "SellingGeneralAndAdministrativeExpense")],
    "operating_income": [("us-gaap", "OperatingIncomeLoss")],
    "net_income": [("us-gaap", "NetIncomeLoss")],
    "ebit": [
        ("us-gaap", "OperatingIncomeLoss"),
        ("us-gaap", "EarningsBeforeInterestAndTaxes"),
    ],
    "interest_expense": [("us-gaap", "InterestExpense")],
    "pretax_income": [
        ("us-gaap", "IncomeLossFromContinuingOperationsBeforeIncomeTaxes"),
    ],
    "tax_expense": [("us-gaap", "IncomeTaxExpenseBenefit")],
    "total_assets": [("us-gaap", "Assets")],
    "current_assets": [("us-gaap", "AssetsCurrent")],
    "total_liabilities": [("us-gaap", "Liabilities")],
    "current_liabilities": [("us-gaap", "LiabilitiesCurrent")],
    "total_equity": [("us-gaap", "StockholdersEquity")],
    "retained_earnings": [("us-gaap", "RetainedEarningsAccumulatedDeficit")],
    "receivables": [
        ("us-gaap", "AccountsReceivableNetCurrent"),
        ("us-gaap", "ReceivablesNetCurrent"),
    ],
    "ppe_net": [("us-gaap", "PropertyPlantAndEquipmentNet")],
    "depreciation": [
        ("us-gaap", "DepreciationDepletionAndAmortization"),
        ("us-gaap", "Depreciation"),
    ],
    "long_term_debt": [("us-gaap", "LongTermDebt")],
    "short_term_debt": [
        ("us-gaap", "DebtCurrent"),
        ("us-gaap", "ShortTermBorrowings"),
    ],
    "operating_cash_flow": [
        ("us-gaap", "NetCashProvidedByUsedInOperatingActivities"),
    ],
    "capital_expenditure": [
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
    ],
    "shares_outstanding": [
        ("us-gaap", "CommonStockSharesOutstanding"),
        ("dei", "EntityCommonStockSharesOutstanding"),
    ],
}

#: Metrics whose facts are point-in-time (balance sheet) rather than flows.
INSTANT_METRICS = frozenset({
    "total_assets", "current_assets", "total_liabilities",
    "current_liabilities", "total_equity", "retained_earnings",
    "receivables", "ppe_net", "long_term_debt", "short_term_debt",
    "shares_outstanding",
})


def _parse_date(value: object) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None
    return dt.replace(tzinfo=timezone.utc)


def _is_annual_form(form: object) -> bool:
    text = str(form or "").strip().upper()
    return any(text == root or text.startswith(root + "/") for root in ANNUAL_FORMS)


def _duration_days(start: object, end: object) -> float | None:
    s, e = _parse_date(start), _parse_date(end)
    if s is None or e is None:
        return None
    return (e - s).total_seconds() / 86400.0


def _fact_sort_key(fact: dict) -> tuple[str, str]:
    """Order facts so the newest filing wins (filed desc, then accn desc)."""
    try:
        filed = str(fact.get("filed") or "")
    except Exception:
        filed = ""
    try:
        accn = str(fact.get("accn") or "")
    except Exception:
        accn = ""
    return (filed, accn)


def _annual_facts(raw_facts: list[dict], *, instant: bool) -> dict[str, dict]:
    """Filter raw XBRL facts to annual filings, keyed by period end.

    Latest filed wins per end date (restatement discipline). Flow facts must
    span 350-380 days; instant facts take any annual-form date. Returns
    ``{end_YYYY-MM-DD: fact}``. Never raises (bad rows skipped).
    """
    by_end: dict[str, dict] = {}
    for fact in raw_facts:
        if not isinstance(fact, dict):
            continue
        try:
            if not _is_annual_form(fact.get("form")):
                continue
            end = str(fact.get("end") or "")[:10]
            if len(end) != 10:
                continue
            if not instant:
                days = _duration_days(fact.get("start"), fact.get("end"))
                if days is None or not 350.0 <= days <= 380.0:
                    continue
            try:
                float(fact.get("val"))  # must be numeric
            except (TypeError, ValueError):
                continue
            prev = by_end.get(end)
            if prev is None or _fact_sort_key(fact) > _fact_sort_key(prev):
                by_end[end] = fact
        except Exception:
            continue
    return by_end


def pick_metric_series(
    companyfacts: dict,
    metric: str,
    *,
    prefer_currency: str = "USD",
    prefer_ends: list[str] | tuple[str, ...] | None = None,
) -> tuple[list[tuple[str, float, dict]], str | None]:
    """Pick (end, value, fact) annual observations for one canonical metric.

    Tries each concept in priority order and returns the FIRST concept with
    any annual facts (avoids mixing tags across periods). When
    ``prefer_ends`` (the anchor metric's ends, e.g. revenue's) is given and
    the winning concept covers any of them, the series is restricted to
    those ends so every metric lines up on the same fiscal periods.
    Returns ``(series_desc_by_end, currency_used)``; empty series when the
    metric is untagged. Never raises.
    """
    try:
        taxonomies = companyfacts.get("facts") or {}
        if not isinstance(taxonomies, dict):
            return [], None
    except Exception:
        return [], None
    for taxonomy, concept in CONCEPT_PRIORITY.get(metric, []):
        try:
            node = (taxonomies.get(taxonomy) or {}).get(concept)
            if not isinstance(node, dict):
                continue
            units = node.get("units") or {}
            if not isinstance(units, dict) or not units:
                continue
            # Prefer the requested currency; else the first unit with facts.
            unit_names = [u for u in units if isinstance(u, str)]
            ordered = ([prefer_currency] if prefer_currency in units else [])
            ordered += [u for u in unit_names if u != prefer_currency]
            instant = metric in INSTANT_METRICS
            for unit in ordered:
                raw = units.get(unit)
                if not isinstance(raw, list) or not raw:
                    continue
                by_end = _annual_facts(raw, instant=instant)
                if not by_end:
                    continue
                if prefer_ends:
                    wanted = [e for e in prefer_ends if e in by_end]
                    if wanted:
                        series = [
                            (e, float(by_end[e]["val"]), by_end[e])
                            for e in sorted(wanted, reverse=True)
                        ]
                        return series, unit
                    # Anchor ends uncovered: fall through to this concept's
                    # own latest ends only if NOTHING else matches below.
                    # (Keep it simple: use own ends; the resolver records
                    # the mismatch in missing_fields via period checks.)
                series = [
                    (e, float(by_end[e]["val"]), by_end[e])
                    for e in sorted(by_end, reverse=True)
                ]
                return series, unit
        except Exception:
            continue
    return [], None


def anchor_ends(
    companyfacts: dict, *, prefer_currency: str = "USD"
) -> tuple[list[str], str | None]:
    """Fiscal period ends anchoring the whole statement (revenue's ends).

    All other metrics prefer these ends so margins/turnover never mix
    periods. Returns ``(ends_desc, currency)``; empty when revenue itself
    is untagged (caller treats statements as unavailable).
    """
    series, currency = pick_metric_series(
        companyfacts, "revenue", prefer_currency=prefer_currency
    )
    return [end for end, _, _ in series], currency


__all__ = [
    "ANNUAL_FORMS",
    "CONCEPT_PRIORITY",
    "INSTANT_METRICS",
    "anchor_ends",
    "pick_metric_series",
]
