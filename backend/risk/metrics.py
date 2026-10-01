"""Risk metrics from daily closes (pure functions, no I/O).

Conventions: returns are daily log returns; volatilities are annualized with
252 sessions; VaR/CVaR are reported as positive loss fractions (0.031 means
"lose 3.1% or more"). Every function returns None when the history is too
short to say anything honest.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252
MIN_OBS = 60


def log_returns(close: pd.Series) -> pd.Series:
    close = close.astype(float)
    close = close[close > 0]
    return np.log(close).diff().dropna()


def _finite(x: float | None, digits: int = 6) -> float | None:
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return round(x, digits) if math.isfinite(x) else None


def annualized_vol(r: pd.Series, window: int | None = None) -> float | None:
    s = r.iloc[-window:] if window else r
    if len(s) < min(MIN_OBS, window or MIN_OBS) * 0.8:
        return None
    return _finite(s.std() * math.sqrt(TRADING_DAYS))


def drawdowns(close: pd.Series) -> dict:
    """Current drawdown from the running peak and the worst drawdown in the series."""
    c = close.astype(float)
    if len(c) < 2:
        return {"current": None, "max": None, "max_start": None, "max_end": None}
    peak = c.cummax()
    dd = c / peak - 1.0
    end = dd.idxmin()
    start = c.loc[:end].idxmax()
    return {
        "current": _finite(dd.iloc[-1]),
        "max": _finite(dd.min()),
        "max_start": str(pd.Timestamp(start).date()),
        "max_end": str(pd.Timestamp(end).date()),
    }


def var_cvar(r: pd.Series, horizon: int = 1, level: float = 0.95) -> dict:
    """Historical VaR/CVaR of h-day returns (overlapping windows), as loss fractions."""
    if len(r) < MIN_OBS + horizon:
        return {"var": None, "cvar": None}
    hr = r.rolling(horizon).sum().dropna() if horizon > 1 else r
    simple = np.expm1(hr.to_numpy())
    cut = np.quantile(simple, 1 - level)
    tail = simple[simple <= cut]
    return {"var": _finite(-cut), "cvar": _finite(-tail.mean()) if len(tail) else None}


def beta_corr(r: pd.Series, market: pd.Series, window: int = TRADING_DAYS) -> dict:
    df = pd.concat([r, market], axis=1, join="inner").dropna().iloc[-window:]
    if len(df) < MIN_OBS:
        return {"beta": None, "correlation": None, "n": len(df)}
    a, m = df.iloc[:, 0], df.iloc[:, 1]
    var_m = m.var()
    beta = a.cov(m) / var_m if var_m > 0 else None
    return {"beta": _finite(beta, 4), "correlation": _finite(a.corr(m), 4), "n": int(len(df))}


def sharpe_sortino(r: pd.Series, window: int = TRADING_DAYS) -> dict:
    """Annualized Sharpe and Sortino of excess-over-zero returns (no risk-free leg)."""
    s = r.iloc[-window:]
    if len(s) < MIN_OBS:
        return {"sharpe": None, "sortino": None, "return_ann": None}
    mean, sd = s.mean(), s.std()
    downside = np.sqrt(np.mean(np.minimum(s, 0) ** 2))
    return {
        "sharpe": _finite(mean / sd * math.sqrt(TRADING_DAYS), 3) if sd > 0 else None,
        "sortino": _finite(mean / downside * math.sqrt(TRADING_DAYS), 3) if downside > 0 else None,
        "return_ann": _finite(math.expm1(mean * TRADING_DAYS)),
    }


def liquidity(close: pd.Series, volume: pd.Series | None, window: int = 63) -> dict:
    if volume is None or len(volume) < 10:
        return {"avg_dollar_volume": None, "amihud": None}
    dv = (close.astype(float) * volume.astype(float)).iloc[-window:]
    r = log_returns(close).iloc[-window:].abs()
    illiq = (r / dv.reindex(r.index).replace(0, np.nan)).dropna()
    return {
        "avg_dollar_volume": _finite(dv.mean(), 0),
        # Amihud: average |return| per $1M traded (higher = less liquid).
        "amihud": _finite(illiq.mean() * 1e6, 6) if len(illiq) else None,
    }


def symbol_risk(frame: pd.DataFrame, market_close: pd.Series | None = None) -> dict:
    """All single-name risk metrics from an OHLCV frame (index: session dates)."""
    close = frame["close"].astype(float)
    r = log_returns(close)
    mret = log_returns(market_close) if market_close is not None and len(market_close) else None
    out = {
        "observations": int(len(r)),
        "vol": {
            "d21": annualized_vol(r, 21),
            "d63": annualized_vol(r, 63),
            "d252": annualized_vol(r, TRADING_DAYS),
        },
        "drawdown": drawdowns(close.iloc[-TRADING_DAYS:]),
        "drawdown_full": drawdowns(close),
        "var_95": {f"d{h}": var_cvar(r, h, 0.95) for h in (1, 5, 21)},
        "var_99": {"d1": var_cvar(r, 1, 0.99)},
        "performance": sharpe_sortino(r),
        "liquidity": liquidity(close, frame["volume"] if "volume" in frame else None),
        "market": beta_corr(r, mret) if mret is not None else {"beta": None, "correlation": None, "n": 0},
        "worst_day": _finite(np.expm1(r.min())) if len(r) else None,
        "best_day": _finite(np.expm1(r.max())) if len(r) else None,
    }
    return out


def portfolio_risk(
    returns: pd.DataFrame, weights: dict[str, float], market: pd.Series | None = None,
    *, window: int = TRADING_DAYS,
) -> dict:
    """Portfolio volatility, VaR, risk contributions and correlations.

    ``returns``: daily log returns, one column per symbol (aligned dates).
    ``weights``: fraction of capital per symbol (normalized here; long-only
    or long/short both work, gross exposure is reported).
    """
    cols = [c for c in returns.columns if c in weights]
    df = returns[cols].dropna(how="all").iloc[-window:].dropna()
    if len(df) < MIN_OBS or not cols:
        raise ValueError(f"need at least {MIN_OBS} common sessions, have {len(df)}")
    w = np.array([weights[c] for c in cols], dtype=float)
    gross = float(np.abs(w).sum())
    if gross <= 0:
        raise ValueError("weights sum to zero")
    w = w / gross
    simple = np.expm1(df.to_numpy())
    cov = np.cov(simple, rowvar=False) * TRADING_DAYS
    cov = np.atleast_2d(cov)
    port_var = float(w @ cov @ w)
    port_vol = math.sqrt(max(port_var, 0.0))
    # Euler decomposition: contributions sum to portfolio volatility.
    mcr = cov @ w / port_vol if port_vol > 0 else np.zeros_like(w)
    contrib = w * mcr
    asset_vol = np.sqrt(np.diag(cov))
    div_ratio = float(np.abs(w) @ asset_vol / port_vol) if port_vol > 0 else None
    daily = pd.Series(simple @ w, index=df.index)
    cut = float(np.quantile(daily, 0.05))
    tail = daily[daily <= cut]
    corr = np.corrcoef(simple, rowvar=False) if len(cols) > 1 else np.array([[1.0]])
    worst = daily.nsmallest(5)
    nav = (1 + daily).cumprod()
    out = {
        "observations": int(len(df)),
        "start": str(pd.Timestamp(df.index[0]).date()),
        "end": str(pd.Timestamp(df.index[-1]).date()),
        "vol_annual": _finite(port_vol),
        "var_95_1d": _finite(-cut),
        "cvar_95_1d": _finite(-tail.mean()),
        "max_drawdown": _finite((nav / nav.cummax() - 1).min()),
        "diversification_ratio": _finite(div_ratio, 3),
        "holdings": [
            {
                "symbol": c,
                "weight": _finite(w[i], 4),
                "vol_annual": _finite(asset_vol[i]),
                "risk_contribution": _finite(contrib[i] / port_vol, 4) if port_vol > 0 else None,
            }
            for i, c in enumerate(cols)
        ],
        "correlation": {"symbols": cols, "matrix": [[_finite(v, 3) for v in row] for row in corr]},
        "worst_days": [{"date": str(pd.Timestamp(d).date()), "return": _finite(v)} for d, v in worst.items()],
    }
    if market is not None and len(market):
        mk = market.reindex(df.index).dropna()
        if len(mk) >= MIN_OBS:
            bc = beta_corr(np.log1p(daily), mk, window)
            out["beta"] = bc["beta"]
            out["market_correlation"] = bc["correlation"]
    return out
