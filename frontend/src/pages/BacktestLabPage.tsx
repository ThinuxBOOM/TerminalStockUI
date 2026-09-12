import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import {
  FORECAST_HORIZONS,
  runBacktest,
  type Backtest,
} from '../api/client';
import ProvenanceBadge from '../components/ProvenanceBadge';
import FreshnessBadge from '../components/FreshnessBadge';
import CalibrationChart from '../components/CalibrationChart';
import Loading from '../components/Loading';
import ErrorState, { StaleBanner } from '../components/ErrorState';

/**
 * Backtest Lab: lightweight walk-forward performance + calibration only (v1).
 * Shows failures as well as successes; never a large backtesting suite.
 */
export default function BacktestLabPage() {
  const [symbol, setSymbol] = useState('AAPL');
  const [horizons, setHorizons] = useState<number[]>([21]);
  const [formError, setFormError] = useState<string | null>(null);

  const lab = useMutation({
    mutationFn: ({ s, h }: { s: string; h: number[] }) => runBacktest(s, h),
  });

  function toggle(h: number) {
    setHorizons((prev) => (prev.includes(h) ? prev.filter((x) => x !== h) : [...prev, h].sort()));
  }

  function run() {
    const s = symbol.trim().toUpperCase();
    if (!s) {
      setFormError('Enter a symbol (e.g. AAPL, 600519.SS, MC.PA).');
      return;
    }
    if (horizons.length === 0) {
      setFormError('Select at least one horizon.');
      return;
    }
    setFormError(null);
    lab.mutate({ s, h: horizons });
  }

  const r: Backtest | undefined = lab.data ?? undefined;

  return (
    <div>
      <h1 className="mb-3 text-sm tracking-widest text-term-muted">BACKTEST LAB · LIGHTWEIGHT (V1)</h1>

      <section className="term-panel p-4">
        <div className="flex flex-wrap items-end gap-3">
          <div>
            <label className="term-label" htmlFor="bt-symbol">
              Symbol
            </label>
            <input
              id="bt-symbol"
              className="term-input mt-1 w-48"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              placeholder="AAPL"
              spellCheck={false}
            />
          </div>
          <div>
            <p className="term-label">Horizons (trading days)</p>
            <div className="mt-1 flex gap-2">
              {FORECAST_HORIZONS.map((h) => (
                <label key={h} className="flex cursor-pointer items-center gap-1 text-sm">
                  <input
                    type="checkbox"
                    checked={horizons.includes(h)}
                    onChange={() => toggle(h)}
                  />
                  {h}d
                </label>
              ))}
            </div>
          </div>
          <button className="term-btn" disabled={lab.isPending} onClick={run}>
            {lab.isPending ? 'RUNNING…' : '▶ RUN BACKTEST'}
          </button>
        </div>
        {formError && <p className="mt-2 text-xs text-term-red">{formError}</p>}
        <p className="mt-2 text-[11px] text-term-muted">
          Walk-forward only, time-ordered splits, corporate-action adjusted prices. Brier score
          (0 = perfect, 0.25 = coin-flip) and ECE (lower = better calibrated).
        </p>
      </section>

      <div className="mt-4">
        {lab.isPending && <Loading label={`backtesting ${symbol.trim().toUpperCase()}…`} />}
        {lab.isError && (
          <ErrorState
            title="Backtest failed"
            detail={
              lab.error instanceof Error
                ? `${lab.error.message} — backend /api/backtest unreachable or rejected.`
                : 'Backend /api/backtest unreachable or rejected.'
            }
            onRetry={run}
          />
        )}
        {!lab.isPending && !lab.isError && !r && (
          <div className="term-panel p-6 text-sm text-term-muted">
            Pick a symbol + horizon and run. Results show Brier score, ECE, and the reliability
            table — failures included.
          </div>
        )}
        {r && <LabResults r={r} />}
      </div>
    </div>
  );
}

function LabResults({ r }: { r: Backtest }) {
  const stale = r.provenance.fallback_used || r.provenance.delay_minutes > 30;
  const verdicts: { ok: boolean; text: string }[] = [];
  if (r.brier === null || r.brier === undefined) {
    verdicts.push({ ok: false, text: 'Brier score unavailable — too few resolved windows.' });
  } else if (r.brier <= 0.25) {
    verdicts.push({ ok: true, text: `Brier ${r.brier.toFixed(4)} beats the coin-flip baseline (0.25).` });
  } else {
    verdicts.push({ ok: false, text: `Brier ${r.brier.toFixed(4)} is worse than coin-flip (0.25) — model adds no skill here.` });
  }
  if (r.ece === null || r.ece === undefined) {
    verdicts.push({ ok: false, text: 'ECE unavailable.' });
  } else if (r.ece <= 0.05) {
    verdicts.push({ ok: true, text: `ECE ${r.ece.toFixed(4)} — well calibrated.` });
  } else if (r.ece <= 0.1) {
    verdicts.push({ ok: true, text: `ECE ${r.ece.toFixed(4)} — roughly calibrated.` });
  } else {
    verdicts.push({ ok: false, text: `ECE ${r.ece.toFixed(4)} — poorly calibrated, treat probabilities with skepticism.` });
  }
  const emptyBins = r.reliability.filter(
    (b) => !(typeof b.mean_predicted === 'number' && Number.isFinite(b.mean_predicted)),
  ).length;
  if (emptyBins > 0) {
    verdicts.push({ ok: false, text: `${emptyBins} calibration bin(s) empty — thin history at those probability levels.` });
  }
  for (const f of r.failures) verdicts.push({ ok: false, text: f });

  return (
    <div className="space-y-4">
      {stale && (
        <StaleBanner detail={`backtest via ${r.provenance.source}, delay ${r.provenance.delay_minutes}m`} />
      )}
      <section className="grid gap-4 md:grid-cols-3">
        <div className="term-panel p-4">
          <p className="term-label">Brier score · {r.symbol}</p>
          <p className="mt-1 text-2xl font-bold">
            {r.brier === null || r.brier === undefined ? '—' : r.brier.toFixed(4)}
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            <ProvenanceBadge p={r.provenance} />
            <FreshnessBadge p={r.provenance} />
          </div>
        </div>
        <div className="term-panel p-4">
          <p className="term-label">ECE (calibration error)</p>
          <p className="mt-1 text-2xl font-bold">
            {r.ece === null || r.ece === undefined ? '—' : r.ece.toFixed(4)}
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            <ProvenanceBadge p={r.provenance} />
            <FreshnessBadge p={r.provenance} />
          </div>
        </div>
        <div className="term-panel p-4">
          <p className="term-label">Coverage</p>
          <p className="mt-1 text-2xl font-bold">
            {r.n_windows ?? '—'}
            <span className="ml-1 text-xs font-normal text-term-muted">windows</span>
          </p>
          <p className="mt-1 text-xs text-term-muted">
            horizons: {r.horizons.length > 0 ? r.horizons.map((h) => `${h}d`).join(', ') : '—'}
          </p>
          <div className="mt-1">
            <ProvenanceBadge p={r.provenance} />
          </div>
        </div>
      </section>

      <section className="term-panel p-4">
        <CalibrationChart rows={r.reliability} title="Reliability diagram" />
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-term-muted">
                <th className="py-1 pr-2">Bin</th>
                <th className="py-1 pr-2">n</th>
                <th className="py-1 pr-2">Mean predicted</th>
                <th className="py-1 pr-2">Fraction positive</th>
              </tr>
            </thead>
            <tbody>
              {r.reliability.length === 0 && (
                <tr className="border-t border-term-border">
                  <td colSpan={4} className="py-2 text-term-muted">
                    No reliability rows returned.
                  </td>
                </tr>
              )}
              {r.reliability.map((b, i) => (
                <tr key={i} className="border-t border-term-border">
                  <td className="py-1 pr-2">
                    {b.bin_low.toFixed(2)}–{b.bin_high.toFixed(2)}
                  </td>
                  <td className="py-1 pr-2">{b.count}</td>
                  <td className="py-1 pr-2">
                    {typeof b.mean_predicted === 'number' && Number.isFinite(b.mean_predicted)
                      ? b.mean_predicted.toFixed(3)
                      : '—'}
                  </td>
                  <td className="py-1 pr-2">
                    {typeof b.fraction_positive === 'number' && Number.isFinite(b.fraction_positive)
                      ? b.fraction_positive.toFixed(3)
                      : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-2">
          <ProvenanceBadge p={r.provenance} />
        </div>
      </section>

      <section className="term-panel p-4">
        <p className="term-label">Verdicts · successes and failures</p>
        <ul className="mt-2 space-y-1 text-xs">
          {verdicts.map((v, i) => (
            <li key={i} className={v.ok ? 'text-term-green' : 'text-term-red'}>
              {v.ok ? '✓ ' : '✕ '}
              {v.text}
            </li>
          ))}
        </ul>
        {r.notes && <p className="mt-2 text-[11px] text-term-muted">{r.notes}</p>}
        <p className="mt-2 text-[11px] text-term-muted">Not investment advice.</p>
      </section>
    </div>
  );
}
