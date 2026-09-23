import React, { useState } from "react";
import {
  clearApiBaseUrlOverride,
  resolveApiBaseUrlWithSource,
  setApiBaseUrlOverride,
} from "../api/baseUrl";

const SOURCE_LABELS = {
  query: "?api= query param",
  override: "this browser",
  "runtime-config": "config.js",
  build: "build-time",
  default: "default",
};

// Lets the user re-point the frontend at any backend (localhost, LAN IP,
// custom port, Docker host) with no rebuild. The choice is stored in
// localStorage and takes effect after the automatic page reload, which
// re-creates every api client with the new base URL.
function BackendConnectionCard() {
  const [{ url: effective, source }] = useState(() => {
    try {
      return resolveApiBaseUrlWithSource();
    } catch {
      return { url: "http://localhost:8000", source: "default" };
    }
  });
  const [draft, setDraft] = useState(effective === "" ? "" : effective);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  function onSave(e) {
    e.preventDefault();
    setError("");
    const value = draft.trim();
    try {
      if (value === "") {
        clearApiBaseUrlOverride();
      } else {
        setApiBaseUrlOverride(value);
      }
      setSaved(true);
      // Reload so client.js / signals.js / news.js pick up the new baseURL.
      window.setTimeout(() => window.location.reload(), 450);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save — edit dist/config.js instead.");
    }
  }

  function onReset() {
    setError("");
    setSaved(false);
    clearApiBaseUrlOverride();
    setDraft("");
    window.setTimeout(() => window.location.reload(), 450);
  }

  return (
    <section className="term-panel min-w-0 p-4" aria-labelledby="backend-connection">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="backend-connection" className="term-label">
          Backend connection (no rebuild needed)
        </h2>
        <span className="term-btn-sm" title="Where the current backend URL came from">
          via {SOURCE_LABELS[source] ?? source}
        </span>
      </div>
      <p className="mt-1 text-xs text-term-muted">
        Currently talking to:{" "}
        <code className="term-num break-all text-term-text">{effective === "" ? "(same origin — /api on this host)" : effective}</code>
      </p>
      <form onSubmit={onSave} className="mt-2 flex min-w-0 flex-col gap-2 sm:flex-row">
        <label htmlFor="backend-url" className="sr-only">
          Backend base URL
        </label>
        <input
          id="backend-url"
          className="term-input min-w-0 flex-1"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="http://192.168.1.20:8000  (empty = auto)"
          spellCheck={false}
          autoComplete="off"
          inputMode="url"
        />
        <span className="flex shrink-0 gap-2">
          <button type="submit" className="term-btn shrink-0 text-xs">
            SAVE + RELOAD
          </button>
          <button type="button" onClick={onReset} className="term-btn-ghost shrink-0 text-xs">
            RESET
          </button>
        </span>
      </form>
      {error ? (
        <p className="mt-1 text-xs text-term-red" role="alert">
          {error}
        </p>
      ) : null}
      {saved && !error ? (
        <p className="mt-1 text-xs text-term-green" role="status">
          ✓ Saved — reloading…
        </p>
      ) : null}
      <p className="mt-2 text-[11px] leading-relaxed text-term-muted">
        Hosting pointers: for a backend on another machine set this URL, then allow this frontend&apos;s origin
        on the backend via <code>CORS_ORIGINS</code> (or <code>FRONTEND_URL</code>). Prefer a file over clicks? Edit{" "}
        <code>public/config.js</code> (dev) or <code>dist/config.js</code> (built) — same effect, no rebuild.
        One-off: open the app with <code>?api=http://host:8000</code> (<code>?api=clear</code> undoes it).
      </p>
    </section>
  );
}

export { BackendConnectionCard as default };
