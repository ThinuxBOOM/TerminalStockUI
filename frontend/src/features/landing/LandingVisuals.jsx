import React, { useEffect, useMemo, useRef, useState } from "react";
import { Check, TrendingUp } from "lucide-react";

// Illustrative quantiles (z-scores of h-day returns, fat-tailed like the
// real model's) for a stock with ~33% annual volatility.
const Z = { "0.05": -1.75, "0.10": -1.3, "0.25": -0.66, "0.50": 0.04, "0.75": 0.68, "0.90": 1.32, "0.95": 1.72 };
const DAILY_SIGMA = 0.021;
const HORIZONS = [1, 7, 14, 21];

function quantilesFor(h, sigma = DAILY_SIGMA) {
  const s = sigma * Math.sqrt(h);
  return Object.fromEntries(Object.entries(Z).map(([k, z]) => [k, Math.expm1(z * s + 0.0004 * h)]));
}

function pct(v, d = 1) {
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v * 100).toFixed(d)}%`;
}

function useInView(threshold = 0.3) {
  const ref = useRef(null);
  const [on, setOn] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof IntersectionObserver === "undefined") {
      setOn(true);
      return undefined;
    }
    const io = new IntersectionObserver(([e]) => { if (e.isIntersecting) { setOn(true); io.disconnect(); } }, { threshold });
    io.observe(el);
    return () => io.disconnect();
  }, [threshold]);
  return [ref, on];
}

// Fan chart drawn from the illustrative quantiles; bands grow from "today".
function MiniFan({ on, highlight = null, height = 190 }) {
  const W = 520;
  const H = 200;
  const pts = [{ h: 0, q: Object.fromEntries(Object.keys(Z).map((k) => [k, 0])) }, ...HORIZONS.map((h) => ({ h, q: quantilesFor(h) }))];
  const lo = -0.16;
  const hi = 0.18;
  const x = (h) => 18 + (h / 21) * (W - 36);
  const y = (v) => 10 + ((hi - v) / (hi - lo)) * (H - 30);
  const band = (a, b) => `M${pts.map((p) => `${x(p.h)},${y(p.q[b])}`).join("L")}L${[...pts].reverse().map((p) => `${x(p.h)},${y(p.q[a])}`).join("L")}Z`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className={`lp-fan w-full ${on ? "is-on" : ""}`} style={{ height }} aria-hidden="true">
      {[-0.1, 0, 0.1].map((t) => <line key={t} x1="18" x2={W - 18} y1={y(t)} y2={y(t)} stroke={t === 0 ? "#2a3446" : "#161d29"} />)}
      <path className="band" d={band("0.05", "0.95")} fill="#3987e5" fillOpacity="0.16" />
      <path className="band" d={band("0.10", "0.90")} fill="#3987e5" fillOpacity="0.26" />
      <path className="band" d={band("0.25", "0.75")} fill="#4d8dff" fillOpacity="0.45" />
      <path className="median" d={`M${pts.map((p) => `${x(p.h)},${y(p.q["0.50"])}`).join("L")}`} fill="none" stroke="#9ec5f4" strokeWidth="2.2" strokeLinecap="round" />
      {HORIZONS.map((h) => (
        <g key={h} opacity={highlight === null || highlight === h ? 1 : 0.35}>
          <line x1={x(h)} x2={x(h)} y1="10" y2={H - 20} stroke={highlight === h ? "#4d8dff" : "#161d29"} strokeWidth={highlight === h ? 1.5 : 1} />
          <circle cx={x(h)} cy={y(pts.find((p) => p.h === h).q["0.50"])} r="4" fill="#9ec5f4" stroke="#10151e" strokeWidth="2" />
          <text x={x(h)} y={H - 4} textAnchor="middle" fontSize="11" fill="#8a94a6">{h}d</text>
        </g>
      ))}
      <circle cx={x(0)} cy={y(0)} r="4.5" fill="#fff" />
    </svg>
  );
}

function Meter({ value, on, className = "" }) {
  return (
    <div className={`relative h-1.5 w-full overflow-hidden rounded-full bg-[#14223b] ${className}`}>
      <div className={`lp-meter-fill absolute inset-y-0 left-0 rounded-full bg-[#4d8dff] ${on ? "is-on" : ""}`} style={{ width: `${value * 100}%` }} />
    </div>
  );
}

function HeroVisual({ coverage }) {
  const [ref, on] = useInView(0.2);
  const q21 = quantilesFor(21);
  return (
    <div ref={ref} className="relative mx-auto w-full max-w-xl">
      <div className="lp-glow" />
      <div className="lp-glass relative rounded-2xl p-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <span className="text-lg font-semibold tracking-tight text-white">NVDA</span>
              <span className="rounded border border-white/10 px-1.5 py-0.5 text-[10px] text-[#8a94a6]">XNAS</span>
            </div>
            <div className="text-xs text-[#8a94a6]">NVIDIA Corporation</div>
          </div>
          <div className="text-right">
            <div className="font-mono text-xl font-semibold text-white">$230.86</div>
            <div className="font-mono text-xs text-[#34d399]">▲ +1.09%</div>
          </div>
        </div>
        <div className="mt-4 flex gap-4 border-b border-white/5 text-xs">
          {["Overview", "Forecast", "Risk", "Fundamentals"].map((t) => (
            <span key={t} className={`-mb-px border-b-2 pb-2 ${t === "Forecast" ? "border-[#4d8dff] text-white" : "border-transparent text-[#8a94a6]"}`}>{t}</span>
          ))}
        </div>
        <div className="mt-3 text-[11px] uppercase tracking-[0.08em] text-[#8a94a6]">Where the price may go</div>
        <MiniFan on={on} />
        <div className="mt-2 grid grid-cols-3 gap-3 border-t border-white/5 pt-3">
          <div>
            <div className="text-[10px] uppercase tracking-[0.08em] text-[#8a94a6]">21-day range</div>
            <div className="mt-0.5 font-mono text-sm font-semibold text-white">{pct(q21["0.10"])} / {pct(q21["0.90"])}</div>
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-[0.08em] text-[#8a94a6]">Outperformance</div>
            <div className="mt-0.5 text-sm font-semibold text-white">Top 12%</div>
            <Meter value={0.88} on={on} className="mt-1" />
          </div>
          <div>
            <div className="text-[10px] uppercase tracking-[0.08em] text-[#8a94a6]">10%+ drop risk</div>
            <div className="mt-0.5 font-mono text-sm font-semibold text-white">16%</div>
          </div>
        </div>
        <div className="mt-3 text-right text-[10px] text-[#5d6778]">Illustrative</div>
      </div>
      <div className="lp-glass lp-float absolute -left-4 top-28 hidden items-center gap-2 rounded-xl px-3 py-2 text-xs sm:flex">
        <span className="grid h-6 w-6 place-items-center rounded-full bg-[#0f2b22] text-[#34d399]"><Check className="h-3.5 w-3.5" /></span>
        <span className="text-[#e5eaf2]"><b className="font-mono">{(coverage * 100).toFixed(1)}%</b> of outcomes inside the 80% range</span>
      </div>
      <div className="lp-glass lp-float-slow absolute -right-3 -bottom-5 hidden items-center gap-2 rounded-xl px-3 py-2 text-xs sm:flex">
        <span className="grid h-6 w-6 place-items-center rounded-full bg-[#14223b] text-[#74a6ff]"><TrendingUp className="h-3.5 w-3.5" /></span>
        <span className="text-[#e5eaf2]">Ranked against 500 stocks, nightly</span>
      </div>
    </div>
  );
}

function RangeExplorer() {
  const [h, setH] = useState(21);
  const [ref, on] = useInView(0.3);
  const q = quantilesFor(h);
  const price = 230.86;
  return (
    <div ref={ref} className="lp-glass rounded-2xl p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium text-white">Range by horizon</span>
        <div className="inline-flex rounded-md border border-white/10 bg-black/20 p-0.5" role="radiogroup" aria-label="Horizon">
          {HORIZONS.map((x) => (
            <button key={x} type="button" role="radio" aria-checked={h === x} onClick={() => setH(x)} className={`rounded px-2.5 py-1 text-xs font-medium ${h === x ? "bg-[#1a2130] text-white" : "text-[#8a94a6] hover:text-white"}`}>{x}D</button>
          ))}
        </div>
      </div>
      <MiniFan on={on} highlight={h} height={200} />
      <div className="mt-2 grid grid-cols-3 gap-3 text-center">
        {[["10th pct", q["0.10"]], ["Median", q["0.50"]], ["90th pct", q["0.90"]]].map(([label, v]) => (
          <div key={label} className="rounded-lg border border-white/5 bg-black/20 px-2 py-2">
            <div className="text-[10px] uppercase tracking-[0.08em] text-[#8a94a6]">{label}</div>
            <div className="font-mono text-sm font-semibold text-white">{pct(v)}</div>
            <div className="font-mono text-[11px] text-[#8a94a6]">${(price * (1 + v)).toFixed(2)}</div>
          </div>
        ))}
      </div>
      <div className="mt-2 text-right text-[10px] text-[#5d6778]">Illustrative stock, ~33% annual volatility</div>
    </div>
  );
}

const RANK_ROWS = [
  ["KLAC", "KLA Corp", 0.99, -0.12, 0.17],
  ["GEV", "GE Vernova", 0.98, -0.13, 0.16],
  ["ISRG", "Intuitive Surgical", 0.91, -0.09, 0.12],
  ["MSFT", "Microsoft", 0.64, -0.08, 0.1],
  ["KO", "Coca-Cola", 0.41, -0.05, 0.06],
  ["MDT", "Medtronic", 0.06, -0.07, 0.08],
];

function RankList() {
  const [ref, on] = useInView(0.3);
  return (
    <div ref={ref} className="lp-glass overflow-hidden rounded-2xl">
      <div className="grid grid-cols-[minmax(0,1fr)_7rem_8rem] gap-3 border-b border-white/5 px-5 py-3 text-[10px] uppercase tracking-[0.08em] text-[#8a94a6]">
        <span>Stock</span><span>Rank (21d)</span><span>80% range</span>
      </div>
      {RANK_ROWS.map(([sym, name, rank, lo, hi], i) => (
        <div key={sym} className="grid grid-cols-[minmax(0,1fr)_7rem_8rem] items-center gap-3 border-b border-white/5 px-5 py-2.5 last:border-0">
          <span className="min-w-0"><span className="font-semibold text-white">{sym}</span><span className="block truncate text-[11px] text-[#8a94a6]">{name}</span></span>
          <span>
            <span className="text-[11px] text-white">{rank >= 0.5 ? `Top ${Math.max(1, Math.round((1 - rank) * 100))}%` : `Bottom ${Math.round(rank * 100)}%`}</span>
            <span className="mt-1 block" style={{ transitionDelay: `${i * 80}ms` }}><Meter value={rank} on={on} /></span>
          </span>
          <svg viewBox="0 0 100 10" className="h-2.5 w-full" aria-hidden="true">
            <rect x="0" y="4.5" width="100" height="1" fill="#1c2431" />
            <rect x={50 + (lo / 0.2) * 50} y="1" width={((hi - lo) / 0.2) * 50} height="8" rx="2" fill="#256abf" />
            <rect x="49.75" y="0" width="0.5" height="10" fill="#8a94a6" />
          </svg>
        </div>
      ))}
      <div className="px-5 py-2 text-right text-[10px] text-[#5d6778]">Illustrative ranking</div>
    </div>
  );
}

const CORR = [[1, 0.62, 0.18, 0.31], [0.62, 1, 0.12, 0.27], [0.18, 0.12, 1, 0.05], [0.31, 0.27, 0.05, 1]];
const NAMES = ["AAPL", "MSFT", "Moutai", "ASML"];

function RiskPreview() {
  const [ref, on] = useInView(0.3);
  const share = [["ASML", 0.38], ["AAPL", 0.27], ["MSFT", 0.24], ["Moutai", 0.11]];
  return (
    <div ref={ref} className="lp-glass grid gap-5 rounded-2xl p-5 sm:grid-cols-2">
      <div>
        <div className="text-sm font-medium text-white">Correlations</div>
        <div className="mt-3 grid grid-cols-[3.5rem_repeat(4,minmax(0,1fr))] gap-1 text-[10px]">
          <span />
          {NAMES.map((n) => <span key={n} className="truncate text-center text-[#8a94a6]">{n}</span>)}
          {CORR.map((row, i) => (
            <React.Fragment key={NAMES[i]}>
              <span className="self-center truncate pr-1 text-right text-[#8a94a6]">{NAMES[i]}</span>
              {row.map((v, j) => (
                <span key={j} className="grid aspect-square place-items-center rounded font-mono text-[10px] text-white" style={{ background: `color-mix(in oklab, #3987e5 ${Math.round(v * 100)}%, #2a3140)` }}>{v.toFixed(2)}</span>
              ))}
            </React.Fragment>
          ))}
        </div>
      </div>
      <div>
        <div className="text-sm font-medium text-white">Share of risk</div>
        <ul className="mt-3 space-y-2.5">
          {share.map(([n, v], i) => (
            <li key={n} className="grid grid-cols-[3.5rem_1fr_2.5rem] items-center gap-2 text-xs">
              <span className="text-[#e5eaf2]">{n}</span>
              <span className="relative h-2 overflow-hidden rounded-full bg-[#151b26]">
                <span className={`lp-meter-fill absolute inset-y-0 left-0 rounded-full bg-[#3987e5] ${on ? "is-on" : ""}`} style={{ width: `${v * 100 / 0.4}%`, transitionDelay: `${i * 90}ms` }} />
              </span>
              <span className="text-right font-mono text-[#e5eaf2]">{Math.round(v * 100)}%</span>
            </li>
          ))}
        </ul>
        <div className="mt-4 grid grid-cols-2 gap-3 border-t border-white/5 pt-3">
          <div><div className="text-[10px] uppercase tracking-[0.08em] text-[#8a94a6]">1-day VaR 95%</div><div className="font-mono text-sm font-semibold text-white">1.49%</div></div>
          <div><div className="text-[10px] uppercase tracking-[0.08em] text-[#8a94a6]">Diversification</div><div className="font-mono text-sm font-semibold text-white">1.85×</div></div>
        </div>
      </div>
      <div className="text-right text-[10px] text-[#5d6778] sm:col-span-2">Illustrative portfolio</div>
    </div>
  );
}

// Live measured record: average 21-day return by model decile.
function DecileProof({ deciles }) {
  const [ref, on] = useInView(0.3);
  const vals = useMemo(() => (deciles ?? []).map((d) => Math.expm1(d.mean_fwd_log_return)), [deciles]);
  if (!vals.length) return null;
  const max = Math.max(...vals) * 1.15;
  return (
    <div ref={ref} className="lp-glass rounded-2xl p-5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-white">Average 21-day return by model rank</span>
        <span className="text-[11px] text-[#8a94a6]">walk-forward, out of sample</span>
      </div>
      <div className="mt-5 flex h-48 items-end gap-2" role="img" aria-label="Average return rises from the lowest-ranked to the highest-ranked decile">
        {vals.map((v, i) => (
          <div key={i} className="flex h-full flex-1 flex-col items-center justify-end gap-1.5">
            {(i === 0 || i === vals.length - 1) ? <span className="font-mono text-[11px] text-white">{pct(v, 2)}</span> : null}
            <div className="w-full max-w-[24px] origin-bottom rounded-t-[4px] bg-[#3987e5] transition-transform duration-700" style={{ height: `${(v / max) * 100}%`, transform: on ? "scaleY(1)" : "scaleY(0.05)", transitionDelay: `${i * 60}ms`, opacity: 0.55 + (i / vals.length) * 0.45 }} />
            <span className="text-[10px] text-[#8a94a6]">{i + 1}</span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex justify-between text-[10px] text-[#5d6778]"><span>lowest-ranked 10%</span><span>highest-ranked 10%</span></div>
    </div>
  );
}

function CoverageProof({ byYear }) {
  const entries = Object.entries(byYear ?? {});
  if (!entries.length) return null;
  const x = (v) => `${Math.max(0, Math.min(100, ((v - 0.7) / 0.2) * 100))}%`;
  return (
    <div className="lp-glass rounded-2xl p-5">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-white">80% range: share of outcomes inside it</span>
        <span className="text-[11px] text-[#8a94a6]">21-day horizon</span>
      </div>
      <ul className="mt-4 space-y-2.5">
        {entries.map(([year, v]) => (
          <li key={year} className="grid grid-cols-[2.5rem_1fr_3.5rem] items-center gap-3 text-xs">
            <span className="font-mono text-[#8a94a6]">{year}</span>
            <span className="relative h-1.5 rounded-full bg-[#151b26]">
              <span className="absolute inset-y-[-4px] w-px bg-[#8a94a6]" style={{ left: x(0.8) }} />
              <span className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[#10151e] bg-[#4d8dff]" style={{ left: x(v) }} />
            </span>
            <span className="text-right font-mono text-white">{(v * 100).toFixed(1)}%</span>
          </li>
        ))}
      </ul>
      <div className="mt-3 text-[10px] text-[#5d6778]">Line marks the 80% target. 2022 was a volatility shock; the model adapts as volatility does.</div>
    </div>
  );
}

export { CoverageProof, DecileProof, HeroVisual, RangeExplorer, RankList, RiskPreview };
