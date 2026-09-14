"""FX conversion + cross-market provenance gate (M7, spec section 4 / Milestone 7).

Gate rules (enforced by :func:`require_fx_provenance`):

1. provenance must exist with a parseable ``as_of`` — otherwise refuse;
2. rates older than 24h (``MAX_AGE_HOURS``) are stale — refuse;
3. fallback rates (``fallback_used=True``) refuse unless the caller passes
   explicit ``allow_fallback=True``.

:func:`rank_cross_market` and :func:`compare_cross_market` call the gate
first and raise :class:`FXProvenanceMissing` (``code ==
"FX_PROVENANCE_MISSING"``) on any violation, so cross-market ranking is
impossible without fresh FX provenance. :func:`convert` is pure math over an
explicit ``rates`` table (direct, inverse, or USD/EUR/CNY triangle path).
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone
from typing import Mapping

CODE = "FX_PROVENANCE_MISSING"
MAX_AGE_HOURS = 24.0

#: Disclosure carried on cross-market decision outputs (rank/compare), which
#: convert raw FX math into a market comparison the UI renders as advice-adjacent.
DISCLOSURE = "Not investment advice. For informational purposes only."


class FXProvenanceMissing(RuntimeError):
    """Raised when cross-market use is attempted without fresh FX provenance."""

    code = CODE

    def __init__(
        self,
        message: str,
        *,
        provenance: dict | None = None,
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.provenance = provenance
        self.retryable = retryable


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_as_of(value: object) -> datetime | None:
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _coerce_provenance(provenance: object) -> dict | None:
    if provenance is None:
        return None
    if hasattr(provenance, "model_dump"):  # Provenance pydantic model
        try:
            data = provenance.model_dump()  # type: ignore[union-attr]
        except Exception:
            return None
    elif isinstance(provenance, Mapping):
        data = dict(provenance)
    else:
        return None
    as_of = _parse_as_of(data.get("as_of"))
    if as_of is None:
        return None
    return {
        "source": data.get("source", "fx"),
        "as_of": as_of,
        "delay_minutes": int(data.get("delay_minutes", 0) or 0),
        "quality_grade": data.get("quality_grade", "C"),
        "fallback_used": bool(data.get("fallback_used", False)),
        "missing_fields": list(data.get("missing_fields") or []),
    }


def require_fx_provenance(
    provenance: object,
    *,
    allow_fallback: bool = False,
    now: datetime | None = None,
    max_age_hours: float = MAX_AGE_HOURS,
) -> dict:
    """Gate cross-market use on fresh FX provenance; return normalized envelope.

    Raises :class:`FXProvenanceMissing` when provenance is missing/invalid,
    when rates are stale (``as_of`` older than ``max_age_hours``), or when
    fallback rates are used without explicit ``allow_fallback=True``.
    """
    norm = _coerce_provenance(provenance)
    if norm is None:
        raise FXProvenanceMissing(
            "FX provenance missing: cross-market ranking requires a rate "
            "payload with a parseable as_of timestamp",
            provenance=None,
        )
    at = now or _utcnow()
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    raw_age_hours = (at - norm["as_of"]).total_seconds() / 3600
    if raw_age_hours < -5 / 60:
        # Future-dated rates (beyond clock-skew tolerance): refuse to rank on
        # them instead of clamping the age to zero and passing the gate.
        raise FXProvenanceMissing(
            "FX rates are future-dated: as_of is ahead of wall-clock; "
            "refresh FX before ranking",
            provenance={**norm, "age_hours": round(raw_age_hours, 2)},
        )
    age_hours = max(0.0, raw_age_hours)
    if age_hours > max_age_hours:
        raise FXProvenanceMissing(
            f"FX rates stale: as_of is {age_hours:.1f}h old "
            f"(limit {max_age_hours:.0f}h); refresh FX before ranking",
            provenance={**norm, "age_hours": round(age_hours, 2)},
        )
    if norm["fallback_used"] and not allow_fallback:
        raise FXProvenanceMissing(
            "FX rates are fallback (ECB reference stub): pass "
            "allow_fallback=True to rank on fallback rates explicitly",
            provenance={**norm, "age_hours": round(age_hours, 2)},
        )
    norm["age_hours"] = round(age_hours, 2)
    return norm


def _split_pair(key: object) -> tuple[str, str] | None:
    if isinstance(key, (tuple, list)) and len(key) == 2:
        return str(key[0]).strip().upper(), str(key[1]).strip().upper()
    if isinstance(key, str):
        text = key.strip().upper().replace(" ", "")
        if "/" in text:
            left, _, right = text.partition("/")
            if left and right:
                return left, right
        if len(text) == 6 and text.isalpha():  # "EURUSD" form
            return text[:3], text[3:]
    return None


def _build_graph(rates: Mapping) -> dict[str, list[tuple[str, float]]]:
    graph: dict[str, list[tuple[str, float]]] = {}
    try:
        entries = dict(rates).items()  # type: ignore[arg-type]
    except (TypeError, ValueError, AttributeError):
        return graph
    for raw_key, raw_value in entries:
        pair = _split_pair(raw_key)
        if pair is None:
            continue
        try:
            rate = float(raw_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if not math.isfinite(rate) or rate <= 0:
            continue
        base, quote = pair
        graph.setdefault(base, []).append((quote, rate))
        graph.setdefault(quote, []).append((base, 1.0 / rate))
    return graph


def convert(amount: float, from_ccy: str, to_ccy: str, rates: Mapping) -> float:
    """Convert ``amount`` from ``from_ccy`` to ``to_ccy`` via ``rates``.

    ``rates`` maps pair keys (``"EUR/USD"``, ``"EURUSD"``, or
    ``(base, quote)`` tuples) to base->quote rates. Direct, inverse, and
    multi-hop (USD/EUR/CNY triangle) paths resolve via shortest-path search.
    Same-currency converts are identity. Raises :class:`ValueError` when no
    conversion path exists.
    """
    if amount is None:
        raise ValueError("convert: amount is required")
    try:
        value = float(amount)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(f"convert: invalid amount {amount!r}") from exc
    if not math.isfinite(value):
        raise ValueError(f"convert: invalid amount {amount!r}")
    frm = (from_ccy or "").strip().upper()
    to = (to_ccy or "").strip().upper()
    if not frm or not to:
        raise ValueError("convert: from_ccy and to_ccy are required")
    if frm == to:
        return value
    if not isinstance(rates, Mapping):
        raise ValueError("convert: rates table is required")
    graph = _build_graph(rates)
    best: dict[str, float] = {frm: 1.0}
    queue: deque[str] = deque([frm])
    while queue:
        node = queue.popleft()
        if node == to:
            out = value * best[node]
            if not math.isfinite(out):
                raise ValueError("convert: conversion overflows finite range")
            return out
        for nxt, edge in graph.get(node, []):
            candidate = best[node] * edge
            if not math.isfinite(candidate):
                continue
            if nxt not in best:
                best[nxt] = candidate
                queue.append(nxt)
    raise ValueError(f"convert: no FX conversion path {frm}->{to}")


def rank_cross_market(
    items: list[dict],
    target_ccy: str,
    rates: Mapping,
    provenance: object,
    *,
    allow_fallback: bool = False,
) -> dict:
    """Rank cross-market items in ``target_ccy``; REFUSES without fresh FX.

    ``items`` are ``{symbol, price, currency}`` dicts. Returns
    ``{target_ccy, count, ranked, provenance}`` sorted by converted value
    descending (missing prices sort last with ``converted=None``). Raises
    :class:`FXProvenanceMissing` when the gate refuses, :class:`ValueError`
    on bad inputs (missing currency, no conversion path).
    """
    target = (target_ccy or "").strip().upper()
    if not target:
        raise ValueError("rank: target_ccy is required")
    gate = require_fx_provenance(provenance, allow_fallback=allow_fallback)
    ranked: list[dict] = []
    for item in items or []:
        row = dict(item)
        symbol = row.get("symbol", "?")
        price = row.get("price")
        ccy = (row.get("currency") or "").strip().upper()
        if not ccy:
            raise ValueError(f"rank: item {symbol!r} is missing currency")
        if price is None:
            ranked.append({
                "symbol": symbol, "price": None, "currency": ccy,
                "converted": None, "target_ccy": target,
            })
            continue
        # JSON safety: any non-finite price (float/Decimal/numpy NaN/inf)
        # sorts last with price/converted None instead of leaking NaN.
        try:
            _probe = float(price)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError):
            _probe = None
        if _probe is not None and not math.isfinite(_probe):
            ranked.append({
                "symbol": symbol, "price": None, "currency": ccy,
                "converted": None, "target_ccy": target,
            })
            continue
        if isinstance(price, bool):
            # bool is an int subclass; 0/1 share prices are degenerate but
            # finite — let convert() handle them without special-casing.
            pass
        try:
            converted = convert(price, ccy, target, rates)
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"rank: invalid price for {symbol!r}: {exc}") from exc
        if not math.isfinite(float(converted)):
            ranked.append({
                "symbol": symbol, "price": price, "currency": ccy,
                "converted": None, "target_ccy": target,
            })
            continue
        ranked.append({
            "symbol": symbol, "price": price, "currency": ccy,
            "converted": converted,
            "target_ccy": target,
        })
    ranked.sort(key=lambda r: (r["converted"] is None, -(r["converted"] or 0.0)))
    return {
        "target_ccy": target,
        "count": len(ranked),
        "ranked": ranked,
        "provenance": {**gate, "as_of": gate["as_of"]},
        "disclosure": DISCLOSURE,
    }


def compare_cross_market(
    item_a: dict,
    item_b: dict,
    target_ccy: str,
    rates: Mapping,
    provenance: object,
    *,
    allow_fallback: bool = False,
) -> dict:
    """Pairwise cross-market compare in ``target_ccy``; gated like rank."""
    result = rank_cross_market(
        [item_a, item_b], target_ccy, rates, provenance, allow_fallback=allow_fallback
    )
    ordered = result["ranked"]
    head = ordered[0] if ordered else None
    winner = head["symbol"] if head is not None and head["converted"] is not None else None
    return {
        "target_ccy": result["target_ccy"],
        "a": ordered[0] if len(ordered) > 0 else None,
        "b": ordered[1] if len(ordered) > 1 else None,
        "winner": winner,
        "provenance": result["provenance"],
        "disclosure": DISCLOSURE,
    }
