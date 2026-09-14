"""Seed-backed canonical instrument registry.

Identity is (exchange_mic, exchange_symbol). Bare tickers are resolved
exchange-aware: suffix first (.SS/.PA/.AS/.BR), then exact US match,
with ambiguity surfaced instead of guessed.
"""

from __future__ import annotations

from backend.instruments.calendars import provider_symbol_for, split_provider_symbol, suffix_for_mic
from backend.instruments.models import Instrument


def _inst(
    mic: str,
    symbol: str,
    name: str,
    currency: str,
    country: str,
    tz: str,
    isin: str | None = None,
    sector: str | None = None,
) -> Instrument:
    return Instrument(
        instrument_id=f"{mic}-{symbol}",
        exchange_mic=mic,
        exchange_symbol=symbol,
        provider_symbol=provider_symbol_for(symbol, mic),
        isin=isin,
        company_name=name,
        currency=currency,
        country=country,
        sector=sector,
        timezone=tz,
        trading_calendar=mic,
        is_active=True,
    )


SEED_INSTRUMENTS: list[Instrument] = [
    _inst("XNAS", "AAPL", "Apple Inc.", "USD", "US", "America/New_York",
          isin="US0378331005", sector="Technology"),
    _inst("XNAS", "MSFT", "Microsoft Corporation", "USD", "US", "America/New_York",
          isin="US5949181045", sector="Technology"),
    _inst("XNAS", "NVDA", "NVIDIA Corporation", "USD", "US", "America/New_York",
          isin="US67066G1040", sector="Technology"),
    # Ticker-ambiguity fixtures: AAP (Advance Auto Parts, NYSE) vs AAPL.
    _inst("XNYS", "AAP", "Advance Auto Parts, Inc.", "USD", "US", "America/New_York",
          isin="US00751Y1064", sector="Consumer Cyclical"),
    _inst("XNYS", "JPM", "JPMorgan Chase & Co.", "USD", "US", "America/New_York",
          isin="US46625H1005", sector="Financial Services"),
    # SSE (exchange_symbol bare; provider_symbol carries .SS suffix)
    _inst("XSHG", "600519", "Kweichow Moutai Co., Ltd.", "CNY", "CN", "Asia/Shanghai",
          isin="CNE0000018R8", sector="Consumer Defensive"),
    _inst("XSHG", "600000", "Shanghai Pudong Development Bank", "CNY", "CN", "Asia/Shanghai",
          isin="CNE000000JP3", sector="Financial Services"),
    _inst("XSHG", "600036", "China Merchants Bank Co., Ltd.", "CNY", "CN", "Asia/Shanghai",
          isin="CNE000001B33", sector="Financial Services"),
    _inst("XSHG", "601318", "Ping An Insurance (Group) Company of China, Ltd.", "CNY", "CN", "Asia/Shanghai",
          isin="CNE000001R84", sector="Financial Services"),
    _inst("XSHG", "600900", "China Yangtze Power Co., Ltd.", "CNY", "CN", "Asia/Shanghai",
          isin="CNE000001G38", sector="Utilities"),
    # Euronext (exchange_symbol bare per M6 SSE convention;
    # provider_symbol carries .PA / .AS / .BR suffix)
    _inst("XPAR", "MC", "LVMH Moet Hennessy Louis Vuitton SE", "EUR", "FR", "Europe/Paris",
          isin="FR0000121014", sector="Consumer Cyclical"),
    _inst("XPAR", "ACA", "Credit Agricole S.A.", "EUR", "FR", "Europe/Paris",
          isin="FR0000045072", sector="Financial Services"),
    _inst("XPAR", "OR", "L'Oreal S.A.", "EUR", "FR", "Europe/Paris",
          isin="FR0000120321", sector="Consumer Defensive"),
    _inst("XAMS", "ASML", "ASML Holding N.V.", "EUR", "NL", "Europe/Amsterdam",
          isin="NL0010273215", sector="Technology"),
    _inst("XAMS", "INGA", "ING Groep N.V.", "EUR", "NL", "Europe/Amsterdam",
          isin="NL0011821202", sector="Financial Services"),
    _inst("XBRU", "UCB", "UCB S.A.", "EUR", "BE", "Europe/Brussels",
          isin="BE0003739530", sector="Healthcare"),
    _inst("XBRU", "ABI", "Anheuser-Busch InBev SA/NV", "EUR", "BE", "Europe/Brussels",
          isin="BE0974293251", sector="Consumer Defensive"),
]


class InstrumentRegistry:
    """In-memory canonical registry (DB-backed later; same interface).

    Seed list + ``EXTRA_SYMBOLS`` env additions (dedupe by
    (exchange_mic, exchange_symbol); seeds win on collision).
    """

    def __init__(self, instruments: list[Instrument] | None = None) -> None:
        if instruments is not None:
            seed = list(instruments)
        else:
            seed = list(SEED_INSTRUMENTS)
            try:
                from backend.instruments.config import extra_instruments as _extras

                for inst in _extras():
                    try:
                        key = (inst.exchange_mic.upper(), inst.exchange_symbol.upper())
                    except Exception:
                        continue
                    if all(
                        not (
                            getattr(s, "exchange_mic", "").upper() == key[0]
                            and getattr(s, "exchange_symbol", "").upper() == key[1]
                        )
                        for s in seed
                    ):
                        seed.append(inst)
            except Exception:
                pass
        self._items: list[Instrument] = seed
        self._by_id: dict[str, Instrument] = {}
        self._by_mic_symbol: dict[tuple[str, str], Instrument] = {}
        self._by_provider: dict[str, Instrument] = {}
        for i in self._items:
            self._by_id[i.instrument_id] = i
            mic = i.exchange_mic.upper()
            sym = i.exchange_symbol.upper()
            self._by_mic_symbol[(mic, sym)] = i
            # Backward-compat aliases: bare <-> suffixed forms resolve both ways.
            # Normalized SSE seeds (exchange_symbol "600519") also answer "600519.SS"
            # and legacy suffixed seeds (e.g. Euronext "MC.PA") also answer bare "MC".
            try:
                suffix = str(suffix_for_mic(mic)).upper()
            except ValueError:
                suffix = ""
            if suffix:
                if not sym.endswith(suffix):
                    self._by_mic_symbol.setdefault((mic, sym + suffix), i)
                    legacy_id = f"{mic}-{sym}{suffix}"
                    self._by_id.setdefault(legacy_id, i)
                else:
                    base = sym[: -len(suffix)] if len(sym) > len(suffix) else sym
                    if base:
                        self._by_mic_symbol.setdefault((mic, base), i)
            if i.provider_symbol:
                self._by_provider[i.provider_symbol.upper()] = i

    def all(self) -> list[Instrument]:
        return list(self._items)

    def get_by_id(self, instrument_id: str) -> Instrument | None:
        hit = self._by_id.get(instrument_id)
        if hit is not None:
            return hit
        # Case-insensitive fallback (IDs are uppercase by construction).
        return self._by_id.get((instrument_id or "").upper())

    def get_by_mic_symbol(self, mic: str, symbol: str) -> Instrument | None:
        try:
            mic_up = str(mic or "").upper()
            sym_up = str(symbol or "").upper()
        except Exception:
            return None
        if not mic_up:
            return None
        key = (mic_up, sym_up)
        hit = self._by_mic_symbol.get(key)
        if hit is not None:
            return hit
        # Backward-compat: try the alternate suffixed/bare form.
        try:
            suffix = str(suffix_for_mic(mic_up)).upper()
        except ValueError:
            return None
        except Exception:
            return None
        if suffix:
            upper = sym_up
            if upper.endswith(suffix):
                base = upper[: -len(suffix)]
                if base:
                    return self._by_mic_symbol.get((mic_up, base))
            else:
                return self._by_mic_symbol.get((mic_up, upper + suffix))
        return None

    def resolve(self, symbol: str, market: str | None = None) -> tuple[Instrument | None, list[Instrument], bool]:
        """Resolve user input to a canonical instrument.

        Returns (instrument, candidates, ambiguous). Suffix-bearing symbols
        resolve to their market; bare tickers prefer exact match, US first.

        Resolution rules (M6/M7, no ticker ambiguity):
        - "600519.SS" resolves globally via provider_symbol (suffix -> XSHG).
        - Bare "600519" + market=XSHG resolves via (mic, exchange_symbol).
        - Bare "600519" globally resolves to the unique XSHG instrument.
        - Euronext mirrors SSE: bare "MC" + market=XPAR and "MC.PA"
          globally both resolve to the XPAR instrument (same for .AS/.BR).
        - Legacy suffixed exchange_symbol forms ("600519.SS" stored) still
          resolve via provider/mic-symbol aliases.
        """
        from backend.instruments.search import search_instruments  # local import: no cycle

        text = str(symbol or "").strip()
        if not text:
            return None, [], False
        upper = text.upper()
        try:
            market_up = str(market).strip().upper() if market else None
        except Exception:
            market_up = None

        # 1. Direct provider-symbol hit (handles .SS/.PA/.AS/.BR exactly).
        direct = self._by_provider.get(upper)
        if direct and (market_up is None or direct.exchange_mic == market_up):
            return direct, [direct], False

        # 1b. Suffix-aware mic+base lookup (backward-compat both forms).
        # "600519.SS" -> (XSHG, "600519"); bare forms skip this branch.
        try:
            base, mic_hint = split_provider_symbol(text)
        except Exception:
            base, mic_hint = upper, None
        if mic_hint is not None and (market_up is None or market_up == mic_hint):
            via_mic = self.get_by_mic_symbol(mic_hint, base)
            if via_mic is not None:
                return via_mic, [via_mic], False
            # Legacy seeds storing the suffixed exchange_symbol (Euronext).
            via_full = self.get_by_mic_symbol(mic_hint, upper)
            if via_full is not None:
                return via_full, [via_full], False

        # 2. Exact exchange-symbol hit (optionally scoped to market).
        exact = [i for i in self._items if i.exchange_symbol.upper() == upper]
        if market_up:
            exact = [i for i in exact if i.exchange_mic == market_up]
        if len(exact) == 1:
            return exact[0], exact, False
        if len(exact) > 1:
            ordered = sorted(exact, key=lambda i: (0 if i.exchange_mic in ("XNAS", "XNYS") else 1, i.exchange_mic))
            return ordered[0], ordered, True

        # 3. Ranked search fallback.
        candidates = search_instruments(self._items, text, market=market_up, limit=5)
        if not candidates:
            return None, [], False
        top = candidates[0]
        ambiguous = len(candidates) > 1 and not candidates[0].exchange_symbol.upper().startswith(upper)
        return top, candidates, ambiguous
