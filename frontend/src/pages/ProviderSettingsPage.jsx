import React from "react";
import BackendConnectionCard from "../components/BackendConnectionCard";
import ProviderSettings from "../features/providers/ProviderSettings";

function ProviderSettingsPage() {
  return (
    <div className="min-w-0 space-y-4">
      <div>
        <h1 className="mb-1 text-sm tracking-widest text-term-muted">DATA HEALTH · PROVIDERS / KEYS / PROFILES</h1>
        <p className="max-w-2xl text-[11px] leading-relaxed text-term-muted">
          Operational screen — provider status, last update, latency, freshness and quality.
          No decorative charts here; every state carries icon + text + timestamp, never color alone.
        </p>
      </div>
      <BackendConnectionCard />
      <ProviderSettings />
    </div>
  );
}

export { ProviderSettingsPage as default };
