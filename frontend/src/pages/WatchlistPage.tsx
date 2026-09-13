import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  TARGET_CURRENCIES,
  getQuote,
  isFreshFxProvenance,
  normalizeTargetCcy,
  rankCrossMarket,
  type Quote,
  type TargetCurrency,
} from '../api/client';
import CurrencyValue from '../components/CurrencyValue';
import ErrorState from '../components/ErrorState';
import FXProvenanceBanner from '../components/FXProvenanceBanner';
import Loading from '../components/Loading';
import MarketStateBadge from '../components/MarketStateBadge';
import ProvenanceBadge from '../components/ProvenanceBadge';

/** M7 default cross-market set: US + Euronext (Paris/Amsterdam/Brussels) + SSE. */
const DEFAULT_SYMBOLS = ['AAPL', 'MC.PA', 'ASML.AS', 'UCB.BR', '600519.SS'];

const GATE_MESSAGE = 'Cross-market comparison unavailable — FX provenance missing';

function normalizeSymbolInput(v: string): string {
  return v.trim().toUpperCase().replace(/\s+/g, '');
}

async function fetchNativeQuotes(symbols: string[]): Promise<(Quote | null)[]> {
  const settled = await Promise.all(
    symbols.map(async (s) => {
      try {
        return await getQuote(s);
      } catch {
        return null;
      }
    }),
  );
  return settled;
}

export default function WatchlistPage() {
  const [symbols, setSymbols] = useState<string[]>(DEFAULT_SYMBOLS);
  const [draft, setDraft] = useState('');
  const [targetCcy, setTargetCcy] = useState<TargetCurrency>('USD');

  const symbolsKey = symbols.join(',');
  const quotesQuery = useQuery({
    queryKey: ['watchlist', 'quotes', symbolsKey],
    queryFn: () => fetchNativeQuotes(symbols),
    enabled: symbols.length > 0,
    staleTime: 30_000,
    retry: false,
  });

  const rankQuery = useQuery({
    queryKey: ['watchlist', 'rank', symbolsKey, targetCcy],
    queryFn: () => rankCrossMarket(symbols, targetCcy),
    enabled: symbols.length > 0,
    staleTime: 30_000,
    retry: false,
  });

  const fxProvenance = rankQuery.data?.fx_provenance ?? null;
  const fxFresh = rankQuery.data ? isFreshFxProvenance(fxProvenance) : false;
  // Gate: never render ranked/converted numbers without fresh FX provenance.
  const gated = rankQuery.isError || !rankQuery.data || !fxFresh;

  function addSymbol() {
    const sym = normalizeSymbolInput(draft);
    if (!sym) return;
    if (symbols.map((s) => s.toUpperCase()).includes(sym)) {
      setDraft('');
      return;
    }
    setSymbols((prev) => [...prev, sym]);
    setDraft('');
  }

  function removeSymbol(sym: string) {
    setSymbols((prev) => prev.filter((s) => s.toUpperCase() !== sym.toUpperCase()));
  }

  return (
    <div>
      <h1 className="mb-3 text-sm tracking-widest text-term-muted">
        WATCHLIST · CROSS-MARKET (FX-GATED)
      </h1>

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <label htmlFor="target-ccy" className="text-xs text-term-muted">
          Target currency
        </label>
        <select
          id="target-ccy"
          className="term-input"
          value={targetCcy}
          onChange={(e) => setTargetCcy(normalizeTargetCcy(e.target.value))}
          aria-label="Target currency for cross-market comparison"
        >
          {TARGET_CURRENCIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            addSymbol();
          }}
        >
          <input
            className="term-input"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Add symbol (e.g. MC.PA)"
            aria-label="Add symbol to watchlist"
          />
          <button className="term-btn" type="submit">
            ADD
          </button>
        </form>
      </div>

      <FXProvenanceBanner
        provenance={fxProvenance}
        targetCcy={targetCcy}
        loading={rankQuery.isLoading}
        error={rankQuery.error}
      />

      {gated ? (
        <div>
          <div
            className="term-panel border-term-red p-4 text-sm text-term-red"
            role="alert"
          >
            {GATE_MESSAGE}
            {rankQuery.error ? (
              <span className="mt-1 block text-xs text-term-muted">
                {rankQuery.error instanceof Error
                  ? rankQuery.error.message
                  : 'FX rank endpoint unreachable.'}{' '}
                Showing native-currency quotes only; no conversion applied.
              </span>
            ) : (
              <span className="mt-1 block text-xs text-term-muted">
                Ranked conversion needs fresh FX (grade A/B, delay ≤ 30m, no
                fallback). Showing native-currency quotes only; no conversion
                applied.
              </span>
            )}
          </div>
          <div className="mt-4">{renderNativeQuotes()}</div>
        </div>
      ) : (
        <div>{renderRanked()}</div>
      )}
    </div>
  );

  function renderNativeQuotes() {
    if (quotesQuery.isLoading) return <Loading label="loading watchlist quotes…" />;
    if (quotesQuery.isError) {
      return (
        <ErrorState
          title="Watchlist quotes unavailable"
          detail={
            quotesQuery.error instanceof Error
              ? quotesQuery.error.message
              : 'Backend unreachable. Check VITE_API_BASE_URL.'
          }
          onRetry={() => void quotesQuery.refetch()}
        />
      );
    }
    const rows = quotesQuery.data ?? [];
    if (rows.length === 0) {
      return (
        <div className="term-panel p-6 text-sm text-term-muted">
          Watchlist is empty. Add a symbol (e.g. MC.PA, ASML.AS, UCB.BR).
        </div>
      );
    }
    return (
      <ul className="term-panel divide-y divide-term-border">
        {symbols.map((sym, i) => {
          const q: Quote | null = rows[i] ?? null;
          if (!q) {
            return (
              <li key={sym} className="flex items-center justify-between p-3">
                <div>
                  <b className="text-term-text">{sym}</b>
                  <span className="ml-2 text-xs text-term-muted">unavailable</span>
                </div>
                <button
                  className="term-btn-ghost text-xs"
                  type="button"
                  onClick={() => removeSymbol(sym)}
                  aria-label={`Remove ${sym}`}
                >
                  REMOVE
                </button>
              </li>
            );
          }
          return (
            <li key={`${sym}-${q.instrument?.exchange_mic ?? ''}`} className="p-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <b className="text-term-green">{q.symbol}</b>
                  <span className="ml-2 text-xs text-term-muted">
                    {q.instrument?.company_name ?? ''}{' '}
                    {q.instrument?.exchange_mic ? `· ${q.instrument.exchange_mic}` : ''}{' '}
                    {q.currency ? `· ${q.currency}` : ''}
                  </span>
                  <span className="ml-2 text-sm">
                    <CurrencyValue value={q.price} currency={q.currency ?? 'USD'} />
                  </span>
                  <span className="ml-2">
                    <MarketStateBadge state={q.market_state} provenance={q.provenance} />
                  </span>
                </div>
                <button
                  className="term-btn-ghost text-xs"
                  type="button"
                  onClick={() => removeSymbol(sym)}
                  aria-label={`Remove ${sym}`}
                >
                  REMOVE
                </button>
              </div>
              <div className="mt-1">
                <ProvenanceBadge p={q.provenance} />
              </div>
            </li>
          );
        })}
      </ul>
    );
  }

  function renderRanked() {
    const data = rankQuery.data;
    if (!data) return null;
    if (rankQuery.isLoading) return <Loading label={`ranking in ${targetCcy}…`} />;
    // Defensive: this branch only renders when fxFresh holds. If the flag
    // ever flips (stale cache win), fall back to the gate — never rank
    // without fresh FX.
    if (!isFreshFxProvenance(data.fx_provenance)) {
      return (
        <div className="term-panel border-term-red p-4 text-sm text-term-red" role="alert">
          {GATE_MESSAGE}
        </div>
      );
    }
    const rows = [...data.ranking].sort((a, b) => {
      const av = a.converted_price ?? null;
      const bv = b.converted_price ?? null;
      if (av === null && bv === null) return 0;
      if (av === null) return 1;
      if (bv === null) return -1;
      return bv - av;
    });
    return (
      <div className="term-panel overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-term-border text-left text-xs text-term-muted">
              <th className="p-2">#</th>
              <th className="p-2">Symbol</th>
              <th className="p-2">Native</th>
              <th className="p-2">Converted ({data.target_ccy})</th>
              <th className="p-2">Market</th>
              <th className="p-2">Provenance</th>
              <th className="p-2">
                <span className="sr-only">Remove</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, idx) => (
              <tr key={`${r.symbol}-${idx}`} className="border-b border-term-border">
                <td className="p-2 text-term-muted">{idx + 1}</td>
                <td className="p-2">
                  <b className="text-term-green">{r.symbol}</b>
                  <span className="ml-2 text-xs text-term-muted">
                    {r.instrument?.company_name ?? ''}{' '}
                    {r.instrument?.exchange_mic ? `· ${r.instrument.exchange_mic}` : ''}
                  </span>
                </td>
                <td className="p-2">
                  <CurrencyValue value={r.price ?? null} currency={r.currency ?? 'USD'} />
                </td>
                <td className="p-2">
                  <b>
                    <CurrencyValue
                      value={r.converted_price ?? null}
                      currency={data.target_ccy}
                    />
                  </b>
                </td>
                <td className="p-2">
                  <span
                    className="text-term-muted"
                    title="Per-row market state is not served on ranked rows — see the native quote on the Security Brief"
                  >
                    —
                  </span>
                </td>
                <td className="p-2">
                  <ProvenanceBadge p={data.fx_provenance ?? r.provenance} />
                </td>
                <td className="p-2">
                  <button
                    className="term-btn-ghost text-xs"
                    type="button"
                    onClick={() => removeSymbol(r.symbol)}
                    aria-label={`Remove ${r.symbol}`}
                  >
                    REMOVE
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="p-2 text-[11px] text-term-muted">
          Ranked by converted price in {data.target_ccy} · FX{' '}
          {data.fx_provenance
            ? `${data.fx_provenance.source} as_of ${data.fx_provenance.as_of}`
            : 'provenance unavailable'}{' '}
          · provenance badge is the conversion FX envelope; native quotes per symbol.
        </p>
      </div>
    );
  }
}
