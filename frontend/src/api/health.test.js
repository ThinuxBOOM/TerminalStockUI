import { afterEach, describe, expect, it } from "vitest";
import { api, getHealth } from "./client";

afterEach(() => {
  delete api.defaults.adapter;
});

describe("getHealth", () => {
  it("accepts providers that have never been called (null latency/last_check)", async () => {
    api.defaults.adapter = async (config) => ({
      status: 200,
      statusText: "OK",
      headers: {},
      config,
      data: {
        status: "ok",
        postgres: "unknown",
        redis: "up",
        version: "0.1.0",
        providers: [
          { provider: "yfinance", state: "up", latency_p50_ms: 120.5, circuit: "closed", last_check: "2026-09-30T16:51:24+00:00" },
          { provider: "xai", state: "unconfigured", latency_p50_ms: null, circuit: "closed", last_check: null },
        ],
      },
    });
    const health = await getHealth();
    expect(health.status).toBe("ok");
    expect(health.providers.map((p) => p.status)).toEqual(["ok", "unconfigured"]);
  });
});
