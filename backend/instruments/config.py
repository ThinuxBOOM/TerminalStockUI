"""Pluggable market registry: YAML venues + env ticker additions.

Single source of truth for ADDING markets/tickers without code edits:

  config/markets.yaml (MARKETS_YAML env overrides the path)
    -> load_market_configs() -> list[MarketConfig]
    -> market_mics() / market_meta() used by calendars/routers as the
       canonical venue set (EXCHANGE_META stays as the built-in compat view).

  EXTRA_SYMBOLS env (comma list, e.g. "GOOGL,MSFT,XLON:VOD.L")
    -> parse_extra_symbols() -> extra Instrument rows appended to the
       registry seed list (MIC defaults to XNAS; "MIC:SYM" pins the venue;
       venue must exist in the YAML set or the built-in map).

No secrets here (venues only). Validation is strict at load (MIC ^[A-Z]{4}$,
suffix ^(\\.[A-Z]{1,4})?$, real ZoneInfo timezone, 3-letter currency) so a
typo fails loudly at startup, never as silent misrouting.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MIC_RE = re.compile(r"^[A-Z]{4}$")
SUFFIX_RE = re.compile(r"^(\.[A-Z]{1,4})?$")
CCY_RE = re.compile(r"^[A-Z]{3}$")

_DEFAULT_YAML = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config",
    "markets.yaml",
)


@dataclass(frozen=True)
class MarketConfig:
    mic: str
    name: str
    provider_suffix: str = ""
    timezone: str = "UTC"
    currency: str = "USD"
    country: str = ""
    delay_minutes: int = 15
    enabled: bool = True
    ingest: bool = True


def _markets_yaml_path() -> str:
    return (os.getenv("MARKETS_YAML", "") or "").strip() or _DEFAULT_YAML


def _validate_market(raw: dict) -> MarketConfig:
    try:
        mic = str(raw.get("mic") or "").strip().upper()
    except Exception:
        mic = ""
    if not MIC_RE.match(mic):
        raise ValueError(f"markets.yaml: bad MIC {raw.get('mic')!r} (want ^[A-Z]{{4}}$)")
    try:
        suffix = str(raw.get("provider_suffix") or "").strip().upper()
    except Exception:
        suffix = ""
    if not SUFFIX_RE.match(suffix):
        raise ValueError(f"markets.yaml [{mic}]: bad provider_suffix {suffix!r}")
    tz = str(raw.get("timezone") or "UTC").strip()
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValueError(f"markets.yaml [{mic}]: unknown timezone {tz!r}") from exc
    ccy = str(raw.get("currency") or "USD").strip().upper()
    if not CCY_RE.match(ccy):
        raise ValueError(f"markets.yaml [{mic}]: bad currency {ccy!r}")
    try:
        delay = int(raw.get("delay_minutes", 15))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"markets.yaml [{mic}]: bad delay_minutes") from exc
    return MarketConfig(
        mic=mic,
        name=str(raw.get("name") or mic),
        provider_suffix=suffix,
        timezone=tz,
        currency=ccy,
        country=str(raw.get("country") or ""),
        delay_minutes=delay,
        enabled=bool(raw.get("enabled", True)),
        ingest=bool(raw.get("ingest", True)),
    )


def _parse_yaml(text: str) -> list[dict]:
    """Minimal YAML subset parser (markets list only; stdlib, no pyyaml dep)."""
    markets: list[dict] = []
    current: dict | None = None
    in_markets = False
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("markets:"):
            in_markets = True
            continue
        if not in_markets:
            continue
        if stripped.startswith("- "):
            if current is not None:
                markets.append(current)
            current = {}
            rest = stripped[2:].strip()
            if rest and ":" in rest:
                k, v = rest.split(":", 1)
                current[k.strip()] = _yaml_scalar(v.strip())
        elif current is not None and ":" in stripped:
            k, v = stripped.split(":", 1)
            current[k.strip()] = _yaml_scalar(v.strip())
    if current is not None:
        markets.append(current)
    return markets


def _yaml_scalar(value: str):
    low = value.strip()
    if low in ("true", "True"):
        return True
    if low in ("false", "False"):
        return False
    if (low.startswith('"') and low.endswith('"')) or (
        low.startswith("'") and low.endswith("'")
    ):
        return low[1:-1]
    try:
        return int(low)
    except (TypeError, ValueError):
        return value.strip().strip('"').strip("'")


@lru_cache(maxsize=4)
def load_market_configs(path: str | None = None) -> tuple[MarketConfig, ...]:
    """Load + validate venue configs (cached; clear cache to hot-reload)."""
    cfg_path = path or _markets_yaml_path()
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ()
    try:
        raws = _parse_yaml(text)
    except Exception:
        return ()
    out: list[MarketConfig] = []
    seen: set[str] = set()
    for raw in raws:
        try:
            cfg = _validate_market(raw if isinstance(raw, dict) else {})
        except ValueError:
            continue
        if cfg.mic in seen:
            continue
        seen.add(cfg.mic)
        out.append(cfg)
    return tuple(out)


def reload_market_configs() -> tuple[MarketConfig, ...]:
    try:
        load_market_configs.cache_clear()
    except Exception:
        pass
    return load_market_configs()


def market_mics(*, enabled_only: bool = True) -> tuple[str, ...]:
    cfgs = load_market_configs()
    if not cfgs:
        return ()
    if enabled_only:
        return tuple(c.mic for c in cfgs if c.enabled)
    return tuple(c.mic for c in cfgs)


def market_meta(mic: str) -> MarketConfig | None:
    try:
        upper = str(mic or "").strip().upper()
    except Exception:
        return None
    for cfg in load_market_configs():
        if cfg.mic == upper:
            return cfg
    return None


def parse_extra_symbols(
    raw: str | None = None, known_mics: set[str] | None = None
) -> list[dict]:
    """Parse EXTRA_SYMBOLS env: "GOOGL, MSFT, XSHG:600519" -> rows.

    Bare tickers default to XNAS; "MIC:SYM" pins the venue. Unknown MICs
    are skipped (never crash startup). Suffix forms ("VOD.L") keep their
    provider symbol; the MIC prefix wins for identity when both given.
    """
    text = raw if raw is not None else (os.getenv("EXTRA_SYMBOLS", "") or "")
    try:
        parts = [p.strip() for p in str(text).split(",")]
    except Exception:
        return []
    try:
        from backend.instruments.calendars import EXCHANGE_META as _BUILTIN
    except Exception:
        _BUILTIN = {}
    known = set(known_mics or set()) | set(_BUILTIN) | {
        c.mic for c in load_market_configs()
    }
    rows: list[dict] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        mic: str | None = None
        sym = part.strip().upper()
        if ":" in sym:
            maybe_mic, maybe_sym = sym.split(":", 1)
            maybe_mic = maybe_mic.strip().upper()
            maybe_sym = maybe_sym.strip().upper()
            if MIC_RE.match(maybe_mic) and maybe_sym:
                mic, sym = maybe_mic, maybe_sym
        if mic is None:
            # Suffix-aware default: infer venue from provider suffix when
            # possible, else XNAS (same default as quotes).
            try:
                from backend.instruments.calendars import split_provider_symbol as _split

                _base, hint = _split(sym)
                mic = hint or "XNAS"
            except Exception:
                mic = "XNAS"
        if mic not in known:
            continue
        key = f"{mic}:{sym}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({"mic": mic, "symbol": sym})
    return rows


def extra_instruments() -> list:
    """Build Instrument rows for EXTRA_SYMBOLS (never raises; [] on miss)."""
    rows = parse_extra_symbols()
    if not rows:
        return []
    try:
        from backend.instruments.calendars import EXCHANGE_META as _BUILTIN
        from backend.instruments.calendars import split_provider_symbol as _split
        from backend.instruments.models import Instrument as _Instrument
    except Exception:
        return []
    out: list = []
    for row in rows:
        try:
            mic, sym = row["mic"], row["symbol"]
            try:
                base, _hint = _split(sym)
                exch = base or sym
            except Exception:
                exch = sym
            meta = market_meta(mic) or _BUILTIN.get(mic, {})
            currency = str(meta.currency if hasattr(meta, "currency") else meta.get("currency", "USD"))[:3]
            tz = str(meta.timezone if hasattr(meta, "timezone") else meta.get("timezone", "UTC"))
            country = str(meta.country if hasattr(meta, "country") else meta.get("country", ""))
            try:
                from backend.instruments.calendars import provider_symbol_for as _psf

                provider = _psf(exch, mic)
            except Exception:
                provider = sym
            out.append(
                _Instrument(
                    instrument_id=f"{mic}-{exch}",
                    exchange_mic=mic,
                    exchange_symbol=exch,
                    provider_symbol=provider,
                    company_name=exch,
                    currency=currency or "USD",
                    country=country or None,
                    timezone=tz or "UTC",
                    trading_calendar=mic,
                    is_active=True,
                )
            )
        except Exception:
            continue
    # Dedupe against identical (mic, symbol).
    seen: set[tuple[str, str]] = set()
    uniq: list = []
    for inst in out:
        try:
            key = (inst.exchange_mic.upper(), inst.exchange_symbol.upper())
        except Exception:
            continue
        if key in seen:
            continue
        seen.add(key)
        uniq.append(inst)
    return uniq


__all__ = [
    "MarketConfig",
    "extra_instruments",
    "load_market_configs",
    "market_meta",
    "market_mics",
    "parse_extra_symbols",
    "reload_market_configs",
]
