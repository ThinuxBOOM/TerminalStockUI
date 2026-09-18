import React, { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowRight,
  BookOpen,
  Check,
  FileText,
  FlaskConical,
  Info,
  LogIn,
  Plug,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Star,
  TrendingUp,
  Zap,
} from "lucide-react";

// ---------------------------------------------------------------------------
// Welcome / landing page (marketing content only — no auth, no gating).
// V2 note: the top-right "Sign in" / "Sign up" buttons are intentionally
// inert (<button disabled>) — routing will be wired when subscriptions land.
// Everything else stays unlocked and links into the live terminal.
// ?from=security/<symbol> (or ?from=/...) is honoured as a friendly
// "back to where you were" link so the Security Brief breadcrumb keeps working.
// ---------------------------------------------------------------------------

const MARKETS = [
  "New York (NYSE)",
  "Nasdaq",
  "Shanghai (SSE)",
  "Paris",
  "Amsterdam",
  "Brussels",
];

const FEATURES = [
  {
    Icon: BookOpen,
    title: "Plain-English forecasts",
    detail: "Up or down in simple words, with the reasons you can actually check.",
    to: "/security/AAPL",
    cta: "See an example",
  },
  {
    Icon: FileText,
    title: "Security Brief",
    detail: "One page per stock: live price, chart, story, and full research trail.",
    to: "/security/AAPL",
    cta: "Open Apple brief",
  },
  {
    Icon: SlidersHorizontal,
    title: "Top Picks screener",
    detail: "Ideas worth a closer look, ranked by the math — not hype.",
    to: "/screener",
    cta: "Browse top picks",
  },
  {
    Icon: Star,
    title: "My List watchlist",
    detail: "Follow the stocks you like. Saved right in this browser.",
    to: "/watchlist",
    cta: "Open my list",
  },
  {
    Icon: FlaskConical,
    title: "Backtest lab",
    detail: "See how calls like this turned out in the past — wins and misses.",
    to: "/backtest",
    cta: "Test an idea",
  },
  {
    Icon: Plug,
    title: "Data health first",
    detail: "Every number shows its source and age. No black boxes.",
    to: "/providers",
    cta: "Check data health",
  },
];

const FAQS = [
  {
    q: "Do I need to know investing words?",
    a: "No. Everything is written in plain words — up, down, and why. If a chart looks complex, read the one-sentence summary above it.",
  },
  {
    q: "Is this telling me what to buy?",
    a: "No. OneMarket gives you a shortlist to research further — not orders and not financial advice. Always do your own research.",
  },
  {
    q: "How are forecasts made? Does AI decide?",
    a: "Math decides. A deterministic engine is the source of truth, and any AI opinion can only nudge the result by at most 20% — often 0%.",
  },
  {
    q: "Where do the numbers come from?",
    a: "Live market feeds. Each number shows its source, time, and freshness (live, delayed, closed, or stale) — so you can trust what you see.",
  },
  {
    q: "Is anything locked or paid right now?",
    a: "Nothing is locked. The plans below are just a preview of V2 — today everything is free to explore, no account needed.",
  },
  {
    q: "Which markets can I look up?",
    a: "Six venues in one search: NYSE, Nasdaq, Shanghai, Paris, Amsterdam and Brussels. Try AAPL, TSLA, 600519.SS or ASML.AS.",
  },
];

function Glow({ className = "", style = {} }) {
  return (
    <div
      aria-hidden="true"
      className={`pointer-events-none absolute ${className}`}
      style={style}
    />
  );
}

function WelcomePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [ticker, setTicker] = useState("");

  const from = params.get("from") || params.get("symbol") || "";
  const returnHref = from
    ? from.startsWith("/") || from.startsWith("security/")
      ? from.startsWith("/")
        ? from
        : `/${from}`
      : `/security/${encodeURIComponent(from)}`
    : "";

  function submitTicker(e) {
    e.preventDefault();
    const sym = ticker.trim().toUpperCase().replace(/\s+/g, "");
    if (!sym) return;
    navigate(`/security/${encodeURIComponent(sym)}`);
  }

  return (
    <div id="top" className="mx-auto max-w-6xl space-y-10">
      {/* ------------------------------------------------ Landing top bar ---
          Logo left · anchors center · Sign in / Sign up top-right (V2 stub).
          Buttons are deliberately disabled: they do nothing until V2 routing. */}
      <div className="term-panel-hero flex flex-wrap items-center justify-between gap-3 px-4 py-3">
        <a href="#top" className="flex min-w-0 items-center gap-2" aria-label="OneMarket — back to top">
          <img
            src="/logo.svg"
            alt=""
            aria-hidden="true"
            className="h-8 w-8 shrink-0 rounded-lg"
            width="32"
            height="32"
          />
          <span className="font-sans text-base font-black tracking-tight text-term-green">
            ONE<span className="text-term-text">MARKET</span>
            <span className="ml-2 hidden font-sans text-2xs font-normal tracking-normal text-term-muted/70 sm:inline">
              EASY INVESTING
            </span>
          </span>
          <span className="ml-1 hidden rounded-full border border-term-border bg-term-bg px-2 py-0.5 text-[10px] font-semibold tracking-widest text-term-muted lg:inline">
            WELCOME
          </span>
        </a>

        <nav className="order-3 flex w-full items-center gap-4 text-xs text-term-muted sm:order-none sm:w-auto" aria-label="On this page">
          <a href="#how" className="hover:text-term-text">How it works</a>
          <a href="#features" className="hover:text-term-text">Features</a>
          <a href="#markets" className="hover:text-term-text">Markets</a>
          <a href="#pricing" className="hidden hover:text-term-text md:inline">Pricing</a>
          <a href="#faq" className="hidden hover:text-term-text md:inline">FAQ</a>
        </nav>

        {/* Top-right auth actions — V2 placeholder. Keep disabled + titled. */}
        <div className="flex shrink-0 items-center gap-2" role="group" aria-label="Account (coming soon)">
          <button
            type="button"
            disabled
            aria-disabled="true"
            title="Coming soon in V2 — sign-in routing will be wired then"
            className="term-btn-ghost inline-flex cursor-not-allowed items-center gap-1.5 text-xs opacity-60"
          >
            <LogIn className="h-3.5 w-3.5" aria-hidden="true" />
            Sign in
          </button>
          <span className="relative inline-flex items-center">
            <button
              type="button"
              disabled
              aria-disabled="true"
              title="Coming soon in V2 — sign-up routing will be wired then"
              className="term-btn inline-flex cursor-not-allowed items-center gap-1.5 px-4 text-xs opacity-70"
            >
              Sign up
              <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <span className="pointer-events-none absolute -top-2.5 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full border border-term-border bg-term-bg px-1.5 py-px text-[9px] font-bold tracking-widest text-term-muted">
              SOON
            </span>
          </span>
        </div>
      </div>

      {returnHref ? (
        <p className="text-xs text-term-muted" role="status">
          You came from <code className="text-term-text">{from}</code> —{" "}
          <Link to={returnHref} className="font-semibold text-term-green hover:underline">
            jump back to it →
          </Link>
        </p>
      ) : null}

      {/* ---------------------------------------------------------- Hero --- */}
      <section className="term-panel-hero relative overflow-hidden p-6 md:p-10" aria-labelledby="welcome-hero">
        <Glow
          className="inset-0"
          style={{
            background:
              "radial-gradient(640px 300px at 18% 0%, rgba(61,220,132,0.16), transparent 60%), radial-gradient(560px 320px at 92% 18%, rgba(86,200,255,0.10), transparent 60%)",
          }}
        />
        <Glow
          className="-top-24 left-1/4 h-48 w-96 rounded-full"
          style={{ background: "rgba(61,220,132,0.12)", filter: "blur(80px)" }}
        />

        <div className="relative grid items-center gap-8 lg:grid-cols-[1.15fr_0.85fr]">
          <div className="min-w-0 max-w-2xl">
            <p className="inline-flex items-center gap-1.5 rounded-full border border-term-border bg-term-bg px-2.5 py-1 text-[11px] font-semibold tracking-widest text-term-green">
              <Sparkles className="h-3 w-3" aria-hidden="true" />
              NEW HERE? START IN 30 SECONDS
            </p>
            <h1 id="welcome-hero" className="mt-3 font-sans text-4xl font-black tracking-tight text-term-text md:text-5xl">
              Investing, explained <span className="text-term-green">simply</span>.
            </h1>
            <p className="mt-3 max-w-xl text-sm leading-relaxed text-term-muted md:text-base">
              Type any company — we show the live price, whether models lean{" "}
              <b className="text-term-text">up or down</b>, and <b className="text-term-text">why in plain words</b>.
              Every number shows its source and age. Free, no account, nothing locked.
            </p>

            <form onSubmit={submitTicker} role="search" aria-label="Look up a stock" className="mt-5 flex max-w-lg flex-col gap-2 sm:flex-row">
              <label htmlFor="welcome-ticker" className="sr-only">Stock ticker or company</label>
              <div className="relative min-w-0 flex-1">
                <Search
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-term-muted"
                  aria-hidden="true"
                />
                <input
                  id="welcome-ticker"
                  className="term-input w-full pl-9 text-base"
                  value={ticker}
                  onChange={(e) => setTicker(e.target.value)}
                  placeholder="Try AAPL, TSLA, 600519.SS, ASML.AS…"
                  spellCheck={false}
                  autoComplete="off"
                  aria-label="Look up a stock by ticker"
                />
              </div>
              <button type="submit" className="term-btn shrink-0 px-5 py-2.5">
                EXPLAIN →
              </button>
            </form>

            <div className="mt-3 flex flex-wrap gap-2 text-xs">
              <Link to="/" className="term-btn text-xs">
                OPEN TERMINAL →
              </Link>
              <Link to="/screener" className="term-btn-ghost text-xs">
                SEE TOP PICKS →
              </Link>
              {returnHref ? (
                <Link to={returnHref} className="term-btn-ghost text-xs">
                  BACK TO {String(from).toUpperCase().slice(0, 24)} →
                </Link>
              ) : null}
            </div>

            <ul className="mt-5 flex flex-wrap gap-2 text-2xs text-term-muted" aria-label="Why trust OneMarket">
              <li className="term-btn-sm inline-flex cursor-default items-center gap-1.5">
                <FlaskConical className="h-3 w-3 text-term-green" aria-hidden="true" />
                Math first · AI capped at 20%
              </li>
              <li className="term-btn-sm inline-flex cursor-default items-center gap-1.5">
                <ShieldCheck className="h-3 w-3 text-term-green" aria-hidden="true" />
                Sources + freshness on everything
              </li>
              <li className="term-btn-sm cursor-default">Not investment advice</li>
            </ul>
          </div>

          {/* Hero side: live-feel preview card + mini stats */}
          <div className="min-w-0 space-y-3">
            <div
              className="term-panel-nested relative overflow-hidden p-5"
              role="img"
              aria-label="Preview of a stock card: Apple at 232 dollars 40 cents, up 1.2 percent, models leaning up over 21 days"
            >
              <Glow
                className="inset-x-0 top-0 h-16"
                style={{ background: "linear-gradient(180deg, rgba(61,220,132,0.10), transparent)" }}
              />
              <div className="relative flex items-center justify-between gap-2">
                <p className="text-xs font-bold tracking-widest text-term-muted">AAPL · APPLE</p>
                <span className="rounded border border-term-green px-2 py-0.5 text-[11px] font-bold text-term-green">
                  ▲ UP 1.2%
                </span>
              </div>
              <p className="term-num relative mt-2 text-3xl font-black text-term-text">$232.40</p>
              <p className="relative mt-1 text-xs leading-relaxed text-term-muted">
                Models lean <b className="text-term-green">up</b> over 21 days · <span className="term-num font-semibold text-term-text">68%</span> ·
                sales trend + calm market
              </p>
              <div className="relative mt-3 h-1.5 w-full overflow-hidden rounded bg-term-bg" aria-hidden="true">
                <div className="h-full w-[68%] rounded bg-term-green" />
              </div>
              <p className="relative mt-2 text-[11px] text-term-muted">
                Source: example feed · 2 min ago · <span className="font-semibold text-term-green">● fresh</span>
              </p>
              <div className="relative mt-3 flex flex-wrap gap-2">
                <span className="term-btn-sm cursor-default">1–21D forecasts</span>
                <span className="term-btn-sm cursor-default">Chart overlays</span>
                <span className="term-btn-sm cursor-default">Why up / why down</span>
              </div>
            </div>

            <dl className="grid grid-cols-3 gap-2 text-center" aria-label="Highlights">
              {[
                ["1–21D", "Short forecasts you can check"],
                ["6", "Markets, one search"],
                ["100%", "Numbers show sources"],
              ].map(([v, label]) => (
                <div key={label} className="term-panel-nested p-3">
                  <dd className="term-num text-lg font-black text-term-green">{v}</dd>
                  <dt className="mt-0.5 text-[11px] leading-snug text-term-muted">{label}</dt>
                </div>
              ))}
            </dl>
          </div>
        </div>
      </section>

      {/* -------------------------------------------------- Markets strip --- */}
      <section id="markets" className="term-panel scroll-mt-28 p-4 md:p-5" aria-labelledby="welcome-markets">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="term-label">ONE SEARCH · 6 MARKETS</p>
            <h2 id="welcome-markets" className="mt-1 text-lg font-extrabold text-term-text">
              From New York to Shanghai in one box
            </h2>
          </div>
          <Link to="/search" className="term-btn-sm shrink-0">
            TRY SEARCH →
          </Link>
        </div>
        <ul className="mt-3 flex flex-wrap gap-2" aria-label="Supported markets">
          {MARKETS.map((m) => (
            <li
              key={m}
              className="term-panel-nested inline-flex items-center px-3 py-1.5 text-xs font-semibold text-term-muted"
            >
              <span className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-term-green" aria-hidden="true" />
              {m}
            </li>
          ))}
        </ul>
        <p className="mt-3 text-xs text-term-muted">
          Try <Link to="/security/AAPL" className="font-semibold text-term-green hover:underline">AAPL</Link>
          {" · "}
          <Link to="/security/TSLA" className="font-semibold text-term-green hover:underline">TSLA</Link>
          {" · "}
          <Link to="/security/600519.SS" className="font-semibold text-term-green hover:underline">600519.SS</Link>
          {" · "}
          <Link to="/security/ASML.AS" className="font-semibold text-term-green hover:underline">ASML.AS</Link>
          {" "}— tickers, company names and suffixes like .SS / .PA / .AS / .BR all work.
        </p>
      </section>

      {/* --------------------------------------------------- How it works --- */}
      <section id="how" className="term-panel scroll-mt-28 p-6" aria-labelledby="welcome-how">
        <p className="term-label">HOW IT WORKS · 30 SECONDS</p>
        <h2 id="welcome-how" className="mt-1 text-xl font-extrabold text-term-text">
          New to stocks? Start here.
        </h2>
        <p className="mt-1 text-sm text-term-muted">
          No jargon, no setup. Three tiny steps and you are reading the market like a friend explains it.
        </p>
        <ol className="mt-4 grid gap-3 md:grid-cols-3">
          <li className="term-panel-nested p-4">
            <p className="term-num text-2xl font-black text-term-green" aria-hidden="true">1</p>
            <h3 className="mt-1 flex items-center gap-1.5 text-sm font-bold text-term-text">
              <Search className="h-4 w-4 text-term-green" aria-hidden="true" />
              Find a stock
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-term-muted">
              Type a company name — Apple, Tesla, Nestlé — and hit Explain.
            </p>
            <Link to="/search" className="term-btn-sm mt-3 inline-block">
              TRY SEARCH →
            </Link>
          </li>
          <li className="term-panel-nested p-4">
            <p className="term-num text-2xl font-black text-term-green" aria-hidden="true">2</p>
            <h3 className="mt-1 flex items-center gap-1.5 text-sm font-bold text-term-text">
              <BookOpen className="h-4 w-4 text-term-green" aria-hidden="true" />
              Read it in plain words
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-term-muted">
              See if models lean up or down over 1–21 days, and exactly why.
            </p>
            <Link to="/security/AAPL" className="term-btn-sm mt-3 inline-block">
              SEE AN EXAMPLE →
            </Link>
          </li>
          <li className="term-panel-nested p-4">
            <p className="term-num text-2xl font-black text-term-green" aria-hidden="true">3</p>
            <h3 className="mt-1 flex items-center gap-1.5 text-sm font-bold text-term-text">
              <Star className="h-4 w-4 text-term-green" aria-hidden="true" />
              Follow it in My List
            </h3>
            <p className="mt-1 text-xs leading-relaxed text-term-muted">
              One tap to follow. Check back any time — saved in this browser.
            </p>
            <Link to="/watchlist" className="term-btn-sm mt-3 inline-block">
              OPEN MY LIST →
            </Link>
          </li>
        </ol>
      </section>

      {/* ------------------------------------------------------ Features --- */}
      <section id="features" className="scroll-mt-28" aria-labelledby="welcome-features">
        <p className="term-label">WHAT YOU GET</p>
        <h2 id="welcome-features" className="mt-1 text-xl font-extrabold text-term-text">
          Everything explained simply
        </h2>
        <p className="mt-1 text-sm text-term-muted">
          A pro-grade terminal underneath, a friendly guide on top. Pick any card to jump straight in.
        </p>
        <ul className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map((f) => (
            <li key={f.title} className="term-panel flex flex-col p-4 transition-colors hover:border-term-border2">
              <span className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-term-border bg-term-bg">
                <f.Icon className="h-5 w-5 text-term-green" aria-hidden="true" />
              </span>
              <h3 className="mt-3 text-sm font-bold text-term-text">{f.title}</h3>
              <p className="mt-1 flex-1 text-xs leading-relaxed text-term-muted">{f.detail}</p>
              <Link
                to={f.to}
                className="mt-3 inline-flex items-center gap-1 text-xs font-semibold text-term-green hover:underline"
              >
                {f.cta} <ArrowRight className="h-3 w-3" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      </section>

      {/* -------------------------------------------------- Why trust us --- */}
      <section className="term-panel-hero relative overflow-hidden p-6 md:p-8" aria-labelledby="welcome-trust">
        <Glow
          className="inset-0"
          style={{
            background:
              "radial-gradient(520px 260px at 85% 100%, rgba(61,220,132,0.12), transparent 60%)",
          }}
        />
        <div className="relative grid gap-6 md:grid-cols-3">
          <div>
            <TrendingUp className="h-5 w-5 text-term-green" aria-hidden="true" />
            <h3 className="mt-2 text-sm font-bold text-term-text">Math first, hype never</h3>
            <p className="mt-1 text-xs leading-relaxed text-term-muted">
              Deterministic models are the source of truth. Forecasts show probability, confidence,
              and the spread between models — wins and misses included.
            </p>
          </div>
          <div>
            <Zap className="h-5 w-5 text-term-green" aria-hidden="true" />
            <h3 className="mt-2 text-sm font-bold text-term-text">AI with a seatbelt</h3>
            <p className="mt-1 text-xs leading-relaxed text-term-muted">
              AI opinions only appear when you ask, and can move a forecast by at most 20%.
              Big disagreements get flagged in plain words.
            </p>
          </div>
          <div>
            <ShieldCheck className="h-5 w-5 text-term-green" aria-hidden="true" />
            <h3 className="mt-2 text-sm font-bold text-term-text">Check everything</h3>
            <p className="mt-1 text-xs leading-relaxed text-term-muted">
              Live price, source, age, and quality grade on every number. If a feed is down we say
              so — we never guess.{" "}
              <Link to="/providers" className="font-semibold text-term-green hover:underline">
                See data health →
              </Link>
            </p>
          </div>
        </div>
        <div className="relative mt-5 flex flex-wrap gap-2 border-t border-term-border pt-4 text-xs">
          <Link to="/forecast/AAPL" className="term-btn-ghost text-xs">
            FULL RESEARCH EXAMPLE →
          </Link>
          <Link to="/backtest" className="term-btn-ghost text-xs">
            HOW GOOD WERE PAST CALLS? →
          </Link>
          <Link to="/" className="term-btn-ghost text-xs">
            OPEN HOME TERMINAL →
          </Link>
        </div>
      </section>

      {/* --------------------------------------------------------- Pricing --- */}
      <section id="pricing" className="term-panel scroll-mt-28 p-6" aria-labelledby="welcome-pricing">
        <p className="term-label text-center">PRICING · REFERENCE ONLY</p>
        <h2 id="welcome-pricing" className="mt-1 text-center text-xl font-extrabold text-term-text">
          Free to explore. Everything open.
        </h2>
        <p className="mx-auto mt-1 max-w-xl text-center text-xs leading-relaxed text-term-muted">
          Nothing is locked right now — no account, no paywall. These plans just show where
          subscriptions are heading in V2.{" "}
          <Link to="/pricing" className="text-term-green hover:underline">
            See live plans + checkout →
          </Link>
        </p>
        <ul className="mt-5 grid gap-3 md:grid-cols-3">
          <li className="term-panel-nested flex flex-col p-4">
            <h3 className="text-sm font-bold text-term-text">Starter</h3>
            <p className="term-num mt-1 text-2xl font-black text-term-text">
              $0 <span className="text-xs font-normal text-term-muted">· free forever</span>
            </p>
            <ul className="mt-3 flex-1 space-y-1.5 text-xs text-term-muted">
              <li className="flex items-start gap-1.5"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-term-green" aria-hidden="true" />Search every market</li>
              <li className="flex items-start gap-1.5"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-term-green" aria-hidden="true" />Plain-words forecasts + charts</li>
              <li className="flex items-start gap-1.5"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-term-green" aria-hidden="true" />My List watchlist</li>
            </ul>
            <Link to="/" className="term-btn mt-4 text-center text-xs">
              START EXPLORING →
            </Link>
          </li>
          <li className="flex flex-col rounded-lg border border-term-green bg-term-panel2 p-4 shadow-panel-lg">
            <h3 className="flex flex-wrap items-center gap-2 text-sm font-bold text-term-green">
              Pro
              <span className="rounded bg-term-green px-1.5 py-0.5 text-[10px] font-bold tracking-widest text-black">
                MOST LOVED · SOON
              </span>
            </h3>
            <p className="term-num mt-1 text-2xl font-black text-term-text">
              $— <span className="text-xs font-normal text-term-muted">· V2</span>
            </p>
            <ul className="mt-3 flex-1 space-y-1.5 text-xs text-term-muted">
              <li className="flex items-start gap-1.5"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-term-green" aria-hidden="true" />Everything in Starter</li>
              <li className="flex items-start gap-1.5"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-term-green" aria-hidden="true" />Deeper research reports</li>
              <li className="flex items-start gap-1.5"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-term-green" aria-hidden="true" />Bigger watchlists + alerts</li>
            </ul>
            <button
              type="button"
              disabled
              aria-disabled="true"
              title="Coming soon in V2 — nothing is gated today"
              className="term-btn mt-4 cursor-not-allowed text-xs opacity-60"
            >
              TRY PRO · COMING SOON
            </button>
          </li>
          <li className="term-panel-nested flex flex-col p-4">
            <h3 className="text-sm font-bold text-term-text">Team</h3>
            <p className="term-num mt-1 text-2xl font-black text-term-text">
              $— <span className="text-xs font-normal text-term-muted">· V2</span>
            </p>
            <ul className="mt-3 flex-1 space-y-1.5 text-xs text-term-muted">
              <li className="flex items-start gap-1.5"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-term-green" aria-hidden="true" />Everything in Pro</li>
              <li className="flex items-start gap-1.5"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-term-green" aria-hidden="true" />Shared lists + workspaces</li>
              <li className="flex items-start gap-1.5"><Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-term-green" aria-hidden="true" />Priority help</li>
            </ul>
            <button
              type="button"
              disabled
              aria-disabled="true"
              title="Coming soon in V2 — nothing is gated today"
              className="term-btn mt-4 cursor-not-allowed text-xs opacity-60"
            >
              CONTACT US · COMING SOON
            </button>
          </li>
        </ul>
      </section>

      {/* --------------------------------------------- Stories + FAQ --- */}
      <section className="grid items-start gap-6 lg:grid-cols-2" aria-labelledby="welcome-stories">
        <div>
          <p className="term-label">BEGINNERS SAY</p>
          <h2 id="welcome-stories" className="mt-1 text-xl font-extrabold text-term-text">
            Loved by first-timers
          </h2>
          <ul className="mt-4 space-y-3">
            {[
              ["I typed Apple and finally understood what “up or down” meant.", "Sam · first-time investor"],
              ["My List is just one tap. So simple I actually use it.", "Priya · student"],
              ["Every number shows its source. I trust what I can check.", "Leo · learning the basics"],
            ].map(([quote, who]) => (
              <li key={who} className="term-panel p-4">
                <figure>
                  <span className="flex gap-0.5" aria-label="5 out of 5 stars" role="img">
                    {Array.from({ length: 5 }).map((_, i) => (
                      <Star key={i} className="h-3.5 w-3.5 fill-term-green text-term-green" aria-hidden="true" />
                    ))}
                  </span>
                  <blockquote className="mt-2 text-sm leading-relaxed text-term-text">“{quote}”</blockquote>
                  <figcaption className="mt-1 text-xs text-term-muted">— {who}</figcaption>
                </figure>
              </li>
            ))}
          </ul>
        </div>

        <div id="faq" className="scroll-mt-28">
          <p className="term-label">QUESTIONS</p>
          <h2 id="welcome-faq" className="mt-1 text-xl font-extrabold text-term-text">
            Simple answers
          </h2>
          <div className="mt-4 space-y-2">
            {FAQS.map((f) => (
              <details key={f.q} className="term-panel group p-3">
                <summary className="cursor-pointer text-sm font-semibold text-term-text marker:text-term-green">
                  {f.q}
                </summary>
                <p className="mt-1.5 text-xs leading-relaxed text-term-muted">{f.a}</p>
              </details>
            ))}
          </div>
          <p className="mt-3 flex items-start gap-1.5 text-[11px] leading-relaxed text-term-muted">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            Still curious? Open the{" "}
            <Link to="/providers" className="font-semibold text-term-green hover:underline">
              data-health page
            </Link>
            {" "}to see every source, or run a{" "}
            <Link to="/backtest" className="font-semibold text-term-green hover:underline">
              backtest
            </Link>
            {" "}to judge past calls yourself.
          </p>
        </div>
      </section>

      {/* ------------------------------------------------------ Final CTA --- */}
      <section className="term-panel-hero relative overflow-hidden p-6 text-center md:p-10" aria-labelledby="welcome-start">
        <Glow
          className="inset-0"
          style={{
            background:
              "radial-gradient(600px 260px at 50% 0%, rgba(61,220,132,0.15), transparent 65%)",
          }}
        />
        <div className="relative">
          <p className="term-label inline-flex items-center gap-1.5 rounded-full border border-term-border bg-term-bg px-2.5 py-1">
            <Zap className="h-3 w-3 text-term-green" aria-hidden="true" />
            FREE · NO ACCOUNT · NOTHING LOCKED
          </p>
          <h2 id="welcome-start" className="mx-auto mt-3 max-w-2xl text-2xl font-black tracking-tight text-term-text md:text-3xl">
            Ready? Look up your first stock <span className="text-term-green">in 30 seconds.</span>
          </h2>
          <p className="mx-auto mt-2 max-w-xl text-sm leading-relaxed text-term-muted">
            Just type a company and read it in plain words. Deterministic analytics are the source
            of truth — AI opinions stay bounded and capped at 20%.
          </p>
          <div className="mt-5 flex flex-wrap justify-center gap-2">
            <Link to="/search" className="term-btn px-5 text-sm">
              FIND A STOCK →
            </Link>
            <Link to="/" className="term-btn-ghost text-sm">
              OPEN HOME TERMINAL
            </Link>
          </div>
          <p className="mt-3 text-[11px] text-term-muted">
            Not investment advice. For learning and research only.
          </p>
        </div>
      </section>

      <footer role="contentinfo" className="border-t border-term-border px-2 pb-6 pt-3 text-center text-[11px] leading-relaxed text-term-muted">
        Plain-English stock insights — no jargon needed. Numbers show their source and freshness.
        AI opinions are bounded and capped at 20%. Not investment advice.
      </footer>
    </div>
  );
}

export { WelcomePage as default };
