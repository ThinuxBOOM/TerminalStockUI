"""Ranked, exchange-aware instrument search.

Ranking: exact symbol > prefix symbol > substring symbol > company-name match.
Never resolves by bare ticker alone without ranking candidates.
"""

from __future__ import annotations

from backend.instruments.models import Instrument


def search_instruments(
    instruments: list[Instrument],
    query: str,
    market: str | None = None,
    limit: int = 10,
) -> list[Instrument]:
    q = str(query or "").strip().upper()
    if not q:
        return []
    try:
        limit = max(1, min(int(limit or 10), 50))
    except (TypeError, ValueError):
        limit = 10

    pool = list(instruments or [])
    if market:
        try:
            market_up = str(market).strip().upper()
        except Exception:
            market_up = ""
        if market_up:
            pool = [i for i in pool if getattr(i, "exchange_mic", "") == market_up]
        else:
            pool = []

    scored: list[tuple[int, Instrument]] = []
    for inst in pool:
        sym = inst.exchange_symbol.upper()
        prov = (inst.provider_symbol or "").upper()
        name = (inst.company_name or "").upper()
        if q == sym or q == prov:
            scored.append((0, inst))
        elif sym.startswith(q) or prov.startswith(q):
            scored.append((1, inst))
        elif q in sym or q in prov:
            scored.append((2, inst))
        elif q in name:
            scored.append((3, inst))
    scored.sort(key=lambda item: (item[0], item[1].exchange_symbol))
    return [inst for _, inst in scored[:limit]]
