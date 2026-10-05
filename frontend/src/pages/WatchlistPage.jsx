import React, { useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Download, Plus, Upload } from "lucide-react";
import { TARGET_CURRENCIES, extractBackendDetail, isFreshFxProvenance, rankCrossMarket } from "../api/client";
import useWatchlist from "../hooks/useWatchlist";
import Card from "../components/ui/Card";
import Segmented from "../components/ui/Segmented";
import WatchlistTable from "../components/home/WatchlistTable";
import { fmtMoney, fmtPct } from "../utils/format";

const HORIZONS = [1, 7, 14, 21].map((h) => ({ value: h, label: `${h}D` }));

function OneCurrency({ symbols }) {
  const [target, setTarget] = useState("USD");
  const q = useQuery({
    queryKey: ["watchlist-rank", [...symbols].sort().join(","), target],
    queryFn: ({ signal }) => rankCrossMarket([...symbols].sort(), target, { signal }),
    enabled: symbols.length > 1,
    placeholderData: keepPreviousData,
    staleTime: 30000,
    retry: false,
  });
  const fresh = isFreshFxProvenance(q.data?.fx_provenance);
  const rows = q.data?.ranking ?? [];
  return (
    <Card
      title="In one currency"
      subtitle="Prices converted with live exchange rates, ranked by day change"
      actions={<Segmented options={TARGET_CURRENCIES.map((c) => ({ value: c, label: c }))} value={target} onChange={setTarget} ariaLabel="Currency" size="xs" />}
    >
      {symbols.length < 2 ? <p className="text-sm text-term-muted">Add stocks from more than one market to compare them in one currency.</p>
        : q.isError || (q.data && !fresh) ? (
          <p className="text-sm text-term-amber">Exchange rates are stale or unavailable, so prices stay in their own currencies. {q.isError ? extractBackendDetail(q.error, "") : ""}</p>
        ) : q.isLoading ? <div className="h-24 animate-pulse rounded-md bg-term-panel2" /> : (
          <table className="term-table">
            <thead><tr><th>Stock</th><th className="text-right">Native</th><th className="text-right">{target}</th><th className="text-right">Day</th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol}>
                  <td className="font-semibold text-term-text">{r.symbol}</td>
                  <td className="term-num text-right text-term-muted">{fmtMoney(r.price, r.currency || "USD")}</td>
                  <td className="term-num text-right text-term-text">{fmtMoney(r.converted_price, target)}</td>
                  <td className={`term-num text-right ${r.change_pct > 0 ? "text-term-green" : r.change_pct < 0 ? "text-term-red" : "text-term-muted"}`}>{fmtPct(typeof r.change_pct === "number" ? r.change_pct / 100 : null, 2, { signed: true })}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
    </Card>
  );
}

function WatchlistPage() {
  const { symbols, add, remove, clear, exportJSON, importJSON } = useWatchlist();
  const [draft, setDraft] = useState("");
  const [horizon, setHorizon] = useState(21);
  const [message, setMessage] = useState(null);
  const fileRef = useRef(null);
  const sorted = [...symbols].sort();

  function onAdd(e) {
    e.preventDefault();
    const s = draft.trim().toUpperCase().replace(/\s+/g, "");
    if (s) add(s, "manual");
    setDraft("");
  }
  function onExport() {
    const blob = new Blob([JSON.stringify(exportJSON(), null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "watchlist.json";
    a.click();
    URL.revokeObjectURL(a.href);
  }
  async function onImport(e) {
    setMessage(null);
    const f = e.target.files?.[0];
    if (!f) return;
    try {
      const parsed = JSON.parse(await f.text());
      const arr = Array.isArray(parsed) ? parsed : parsed?.symbols;
      if (!Array.isArray(arr)) throw new Error("Expected a JSON array of tickers.");
      importJSON(arr.map(String));
      setMessage(`Imported ${arr.length} symbols.`);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Invalid file.");
    } finally {
      e.target.value = "";
    }
  }

  return (
    <div className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="type-page">Watchlist</h1>
          <p className="mt-1 text-sm text-term-muted">Live prices with each stock&apos;s model rank, range and drop risk. Saved in this browser for your account.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <form className="flex gap-2" onSubmit={onAdd}>
            <input className="term-input w-36 uppercase" value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Add symbol" aria-label="Add symbol" spellCheck={false} />
            <button type="submit" className="term-btn"><Plus className="h-4 w-4" aria-hidden="true" />Add</button>
          </form>
          <button type="button" className="term-btn-ghost" onClick={onExport} disabled={!symbols.length}><Download className="h-4 w-4" aria-hidden="true" />Export</button>
          <button type="button" className="term-btn-ghost" onClick={() => fileRef.current?.click()}><Upload className="h-4 w-4" aria-hidden="true" />Import</button>
          <input ref={fileRef} type="file" accept="application/json" className="hidden" onChange={onImport} />
        </div>
      </header>
      {message ? <p className="text-xs text-term-muted">{message}</p> : null}

      <Card
        title={`${symbols.length} stock${symbols.length === 1 ? "" : "s"}`}
        actions={
          <>
            <Segmented options={HORIZONS} value={horizon} onChange={setHorizon} ariaLabel="Horizon" size="xs" />
            {symbols.length ? <button type="button" className="term-btn-sm" onClick={() => { if (window.confirm("Remove every stock from your watchlist?")) clear(); }}>Clear</button> : null}
          </>
        }
        pad={false}
      >
        <WatchlistTable symbols={sorted} onRemove={remove} horizon={horizon} />
      </Card>

      <OneCurrency symbols={sorted} />
    </div>
  );
}

export { WatchlistPage as default };
