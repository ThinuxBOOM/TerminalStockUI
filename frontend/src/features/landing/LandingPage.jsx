import React from "react";
import { Link } from "react-router-dom";
import { Activity, ArrowRight, Database, FlaskConical, Gauge, LineChart, Plus, ShieldCheck, SlidersHorizontal } from "lucide-react";
import Reveal from "./Reveal.jsx";
import useModelSummary from "./useModelSummary";
import { CoverageProof, DecileProof, HeroVisual, RangeExplorer, RankList, RiskPreview } from "./LandingVisuals.jsx";

const MARKETS = ["NYSE", "Nasdaq", "Shanghai", "Euronext Paris", "Euronext Amsterdam", "Euronext Brussels"];

function pctTxt(v, d = 1, signed = false) {
  if (typeof v !== "number") return "—";
  return `${signed && v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;
}

function Nav() {
  return (
    <header className="sticky top-0 z-40 border-b border-white/5 bg-[#07090f]/70 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4">
        <Link to="/" className="flex items-center gap-2.5" aria-label="OneMarket home">
          <img src="/logo.svg" alt="" className="h-7 w-7" width="28" height="28" />
          <span className="text-[15px] font-semibold tracking-tight text-white">OneMarket</span>
        </Link>
        <nav aria-label="Sections" className="hidden items-center gap-7 text-sm text-[#8a94a6] md:flex">
          <a href="#forecasts" className="hover:text-white">Forecasts</a>
          <a href="#ranking" className="hover:text-white">Ranking</a>
          <a href="#risk" className="hover:text-white">Risk</a>
          <a href="#proof" className="hover:text-white">Track record</a>
          <a href="#faq" className="hover:text-white">FAQ</a>
        </nav>
        <div className="flex items-center gap-2">
          <Link to="/login" className="hidden whitespace-nowrap rounded-md px-3 py-1.5 text-sm text-[#e5eaf2] hover:bg-white/5 sm:inline-flex">Sign in</Link>
          <Link to="/app" className="inline-flex items-center gap-1.5 whitespace-nowrap rounded-md bg-[#4d8dff] px-3 py-1.5 text-sm font-semibold text-white hover:bg-[#74a6ff]">
            <span className="sm:hidden">Open</span><span className="hidden sm:inline">Open terminal</span> <ArrowRight className="h-4 w-4" aria-hidden="true" />
          </Link>
        </div>
      </div>
    </header>
  );
}

function Hero({ model }) {
  const h21 = model.horizons["21"] ?? model.horizons[21] ?? {};
  const h1 = model.horizons["1"] ?? model.horizons[1] ?? {};
  const h7 = model.horizons["7"] ?? model.horizons[7] ?? {};
  return (
    <section className="relative">
      <div className="lp-grid" />
      <div className="relative mx-auto grid max-w-6xl items-center gap-14 px-4 pb-20 pt-16 md:pt-24 lg:grid-cols-[1.05fr_1fr]">
        <div>
          <Reveal>
            <span className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.03] px-3 py-1 text-xs text-[#9ec5f4]">
              <span className="h-1.5 w-1.5 rounded-full bg-[#34d399]" aria-hidden="true" />
              Retrained weekly on {model.universe_size} S&amp;P 500 stocks
            </span>
          </Reveal>
          <Reveal delay={80}>
            <h1 className="mt-6 text-[clamp(2.6rem,6vw,4.6rem)] font-semibold leading-[1.02] tracking-[-0.035em] text-white">
              Stock forecasts <span className="lp-gradient-text">you can check.</span>
            </h1>
          </Reveal>
          <Reveal delay={160}>
            <p className="mt-6 max-w-xl text-lg leading-relaxed text-[#a3adbf]">
              OneMarket forecasts how far every stock could move, ranks the market against itself every night, and grades itself on years of data it never saw. Every number shows its track record.
            </p>
          </Reveal>
          <Reveal delay={240}>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <Link to="/app" className="inline-flex items-center gap-2 rounded-lg bg-[#4d8dff] px-5 py-3 text-sm font-semibold text-white shadow-[0_10px_40px_rgba(77,141,255,0.35)] hover:bg-[#74a6ff]">
                Open the terminal <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
              <a href="#proof" className="inline-flex items-center gap-2 rounded-lg border border-white/10 px-5 py-3 text-sm font-medium text-white hover:bg-white/5">
                See the track record
              </a>
            </div>
          </Reveal>
          <Reveal delay={320}>
            <dl className="mt-12 grid max-w-xl grid-cols-3 gap-6 border-t border-white/5 pt-6">
              <div className="flex flex-col gap-1">
                <dt className="text-xs leading-snug text-[#8a94a6]">of outcomes inside the 80% range</dt>
                <dd className="order-first font-mono text-2xl font-semibold text-white">{pctTxt(h21.range_coverage_80)}</dd>
              </div>
              <div className="flex flex-col gap-1">
                <dt className="text-xs leading-snug text-[#8a94a6]">better drop-risk calls than the base rate</dt>
                <dd className="order-first font-mono text-2xl font-semibold text-white">{pctTxt(h7.drop_risk_skill, 1, true)}</dd>
              </div>
              <div className="flex flex-col gap-1">
                <dt className="text-xs leading-snug text-[#8a94a6]">t-stat of the next-day ranking edge</dt>
                <dd className="order-first font-mono text-2xl font-semibold text-white">{typeof h1.out_ic_t === "number" ? h1.out_ic_t.toFixed(1) : "—"}</dd>
              </div>
            </dl>
          </Reveal>
        </div>
        <Reveal delay={200}>
          <HeroVisual coverage={h21.range_coverage_80 ?? 0.79} />
        </Reveal>
      </div>
      <div className="relative border-y border-white/5 bg-white/[0.015] py-4">
        <div className="lp-marquee mx-auto flex max-w-6xl flex-wrap items-center justify-center gap-x-10 gap-y-2 px-4 text-sm text-[#5d6778]">
          <span className="text-xs uppercase tracking-[0.12em] text-[#8a94a6]">Markets</span>
          {MARKETS.map((m) => <span key={m}>{m}</span>)}
        </div>
      </div>
    </section>
  );
}

function Feature({ id, eyebrow, title, children, visual, flip = false, points = [] }) {
  return (
    <section id={id} className="scroll-mt-20 py-24">
      <div className={`mx-auto grid max-w-6xl items-center gap-12 px-4 lg:grid-cols-2 ${flip ? "lg:[&>*:first-child]:order-2" : ""}`}>
        <Reveal>
          <p className="text-sm font-medium text-[#74a6ff]">{eyebrow}</p>
          <h2 className="mt-3 text-[clamp(1.9rem,3.6vw,2.8rem)] font-semibold leading-[1.08] tracking-[-0.03em] text-white">{title}</h2>
          <div className="mt-5 space-y-4 text-base leading-relaxed text-[#a3adbf]">{children}</div>
          {points.length ? (
            <ul className="mt-7 grid gap-3 sm:grid-cols-2">
              {points.map(([Icon, text]) => (
                <li key={text} className="flex items-start gap-2.5 text-sm text-[#cdd5e1]">
                  <Icon className="mt-0.5 h-4 w-4 shrink-0 text-[#74a6ff]" aria-hidden="true" />
                  {text}
                </li>
              ))}
            </ul>
          ) : null}
        </Reveal>
        <Reveal delay={120}>{visual}</Reveal>
      </div>
    </section>
  );
}

function Proof({ model }) {
  const h21 = model.horizons["21"] ?? model.horizons[21] ?? {};
  return (
    <section id="proof" className="relative scroll-mt-20 border-y border-white/5 bg-[#0a0d14] py-24">
      <div className="lp-grid opacity-60" />
      <div className="relative mx-auto max-w-6xl px-4">
        <Reveal className="max-w-3xl">
          <p className="text-sm font-medium text-[#74a6ff]">Track record</p>
          <h2 className="mt-3 text-[clamp(1.9rem,3.6vw,2.8rem)] font-semibold leading-[1.08] tracking-[-0.03em] text-white">Graded on data it never saw.</h2>
          <p className="mt-5 text-base leading-relaxed text-[#a3adbf]">
            Each year from {model.first_test_year} was predicted by a model trained only on earlier years, across {model.universe_size} stocks. These are those out-of-sample results, refreshed every time the model retrains, and the same numbers sit next to every forecast in the terminal.
          </p>
        </Reveal>
        <div className="mt-12 grid gap-5 lg:grid-cols-2">
          <Reveal><DecileProof deciles={h21.out_deciles} /></Reveal>
          <Reveal delay={100}><CoverageProof byYear={h21.range_coverage_by_year} /></Reveal>
        </div>
        <Reveal delay={150}>
          <div className="mt-5 grid gap-5 md:grid-cols-[1fr_1.4fr]">
            <div className="lp-glass rounded-2xl p-5">
              <div className="flex items-center gap-2 text-sm font-medium text-white"><ShieldCheck className="h-4 w-4 text-[#34d399]" aria-hidden="true" />What it does well</div>
              <ul className="mt-3 space-y-2 text-sm text-[#a3adbf]">
                <li>Ranges that hold about 80% of outcomes, as promised.</li>
                <li>Drop-risk estimates about 5% sharper than the historical base rate.</li>
                <li>A ranking where higher-ranked stocks have, on average, done better.</li>
              </ul>
            </div>
            <div className="lp-glass rounded-2xl p-5">
              <div className="flex items-center gap-2 text-sm font-medium text-white"><Gauge className="h-4 w-4 text-[#fbbf24]" aria-hidden="true" />What it won&apos;t pretend to do</div>
              <p className="mt-3 text-sm leading-relaxed text-[#a3adbf]">
                Predict whether one stock goes up or down. Across ten years of data, no model we tested beat the simple base rate (stocks rose about {pctTxt(h21.up_share, 0)} of 21-day periods). So the terminal shows that base rate instead of a confident-looking guess, and labels the ranking edge for what it is: small, real, and useful across many stocks, not a call on any single one.
              </p>
            </div>
          </div>
        </Reveal>
      </div>
    </section>
  );
}

const STEPS = [
  [Database, "Data with receipts", "Daily bars and live quotes from six markets. Every number carries its source, timestamp and freshness, and missing data says so."],
  [FlaskConical, "Models, retested weekly", "Volatility and ranking models retrain each week on ten years of history, re-run the walk-forward test, and only then go live."],
  [LineChart, "Research in one place", "Chart, forecast, risk, fundamentals, news and an optional AI summary per stock, plus a screener, watchlist and portfolio risk."],
];

const FAQ = [
  ["Is this investment advice?", "No. OneMarket is a research tool. It shows statistical estimates and their measured accuracy; decisions are yours."],
  ["How accurate are the forecasts?", "It depends on what's forecast. Ranges and drop risk are well calibrated. The outperformance ranking has a small but statistically measurable edge. Up/down direction is no better than the base rate, and the app says so. The Model Lab inside the terminal shows every number."],
  ["Which markets are covered?", "NYSE and Nasdaq (the full S&P 500 is scored nightly), the Shanghai Stock Exchange, and Euronext Paris, Amsterdam and Brussels. The ranking model covers US listings; ranges, drop risk and risk analytics cover every market."],
  ["How often does it update?", "Prices update live (usually 15-minute delayed feeds). Forecast scores refresh after each close, and the models retrain every week."],
  ["Who can use it?", "Accounts are created by the server's administrator. If you've been invited, sign in with the email you were given."],
  ["Does it use AI?", "Optionally. An AI assistant can summarize catalysts and risks for a stock on request. It's kept separate from the measured forecasts and never runs unless you ask."],
];

function LandingPage() {
  const model = useModelSummary();
  const h21 = model.horizons["21"] ?? model.horizons[21] ?? {};
  return (
    <div className="lp min-h-screen font-sans antialiased">
      <Nav />
      <main>
        <Hero model={model} />

        <Feature
          id="forecasts"
          eyebrow="Forecasts"
          title="Ranges, not guesses."
          visual={<RangeExplorer />}
          points={[[Activity, "1, 7, 14 and 21-day horizons"], [Gauge, "Chance of a 10%+ drop"], [LineChart, "Prices for each outcome band"], [ShieldCheck, "Accuracy shown beside every number"]]}
        >
          <p>For every stock, OneMarket estimates how far the price could travel over the next one to twenty-one trading days, built from a volatility model trained across hundreds of stocks.</p>
          <p>In walk-forward testing its 80% range held <b className="text-white">{pctTxt(h21.range_coverage_80)}</b> of real outcomes. That kind of calibration is what makes a range useful for sizing a position or setting a stop.</p>
        </Feature>

        <Feature
          id="ranking"
          eyebrow="Ranking"
          title="The whole market, ranked every night."
          visual={<RankList />}
          flip
          points={[[SlidersHorizontal, "Screen by rank, drop risk, volatility and sector"], [LineChart, "Plain-English reasons for each rank"], [Activity, "Scores refresh after every close"], [FlaskConical, "Edge measured out of sample"]]}
        >
          <p>After each close, every S&amp;P 500 stock is scored on its chance of beating the median stock, using momentum, short-term reversal, trend, volatility and strength against its own sector.</p>
          <p>The edge is small but real: top-ranked stocks went on to beat bottom-ranked ones by <b className="text-white">{pctTxt(h21.out_decile_spread, 2)}</b> per 21 days on average. That makes it a starting point for research, not a buy list.</p>
        </Feature>

        <Feature
          id="risk"
          eyebrow="Risk"
          title="Know what you're holding."
          visual={<RiskPreview />}
          points={[[Gauge, "Value at risk and expected shortfall"], [Activity, "Beta, correlation and drawdowns"], [ShieldCheck, "Position sizing from the forecast range"], [LineChart, "Which holding carries your risk"]]}
        >
          <p>See how a single stock has behaved (volatility, worst drawdowns, value at risk, beta) and how your holdings behave together: which positions carry the risk, how correlated they are, and what a bad day has cost.</p>
        </Feature>

        <Proof model={model} />

        <section className="py-24">
          <div className="mx-auto max-w-6xl px-4">
            <Reveal className="max-w-2xl">
              <p className="text-sm font-medium text-[#74a6ff]">How it works</p>
              <h2 className="mt-3 text-[clamp(1.9rem,3.6vw,2.8rem)] font-semibold leading-[1.08] tracking-[-0.03em] text-white">Built to be checked.</h2>
            </Reveal>
            <div className="mt-12 grid gap-5 md:grid-cols-3">
              {STEPS.map(([Icon, title, text], i) => (
                <Reveal key={title} delay={i * 100}>
                  <div className="lp-glass h-full rounded-2xl p-6">
                    <span className="grid h-10 w-10 place-items-center rounded-xl bg-[#14223b] text-[#74a6ff]"><Icon className="h-5 w-5" aria-hidden="true" /></span>
                    <h3 className="mt-5 text-base font-semibold text-white">{title}</h3>
                    <p className="mt-2 text-sm leading-relaxed text-[#a3adbf]">{text}</p>
                  </div>
                </Reveal>
              ))}
            </div>
          </div>
        </section>

        <section id="faq" className="scroll-mt-20 pb-24">
          <div className="mx-auto max-w-3xl px-4">
            <Reveal><h2 className="text-center text-[clamp(1.6rem,3vw,2.2rem)] font-semibold tracking-[-0.03em] text-white">Questions</h2></Reveal>
            <div className="lp-faq mt-10 divide-y divide-white/5 rounded-2xl border border-white/5 bg-white/[0.015]">
              {FAQ.map(([q, a]) => (
                <details key={q} className="group px-6 py-5">
                  <summary className="flex items-center justify-between gap-4 text-left text-[15px] font-medium text-white">
                    {q}
                    <Plus className="lp-faq-icon h-4 w-4 shrink-0 text-[#8a94a6]" aria-hidden="true" />
                  </summary>
                  <p className="mt-3 text-sm leading-relaxed text-[#a3adbf]">{a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        <section className="pb-24">
          <div className="mx-auto max-w-6xl px-4">
            <Reveal>
              <div className="relative overflow-hidden rounded-3xl border border-white/10 bg-gradient-to-br from-[#14223b] via-[#10151e] to-[#1a1533] px-8 py-16 text-center">
                <div className="lp-glow left-1/2 top-0 -translate-x-1/2" />
                <h2 className="relative text-[clamp(1.9rem,4vw,3rem)] font-semibold tracking-[-0.03em] text-white">Research with numbers you can trust.</h2>
                <p className="relative mx-auto mt-4 max-w-xl text-[#a3adbf]">Sign in to the terminal: forecasts, rankings, risk and the full track record.</p>
                <div className="relative mt-8 flex flex-wrap justify-center gap-3">
                  <Link to="/app" className="inline-flex items-center gap-2 rounded-lg bg-white px-5 py-3 text-sm font-semibold text-[#07090f] hover:bg-[#e5eaf2]">
                    Open the terminal <ArrowRight className="h-4 w-4" aria-hidden="true" />
                  </Link>
                  <Link to="/login" className="inline-flex items-center rounded-lg border border-white/15 px-5 py-3 text-sm font-medium text-white hover:bg-white/5">Sign in</Link>
                </div>
              </div>
            </Reveal>
          </div>
        </section>
      </main>
      <footer className="border-t border-white/5">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-4 px-4 py-8 text-xs text-[#5d6778]">
          <div className="flex items-center gap-2">
            <img src="/logo.svg" alt="" className="h-5 w-5" width="20" height="20" />
            <span className="text-[#8a94a6]">OneMarket</span>
            <span>· model {model.version}</span>
          </div>
          <p className="max-w-xl">Statistical estimates, not investment advice. Past accuracy does not guarantee future results. Market data from free and personal-use sources; prices may be delayed.</p>
        </div>
      </footer>
    </div>
  );
}

export { LandingPage as default };
