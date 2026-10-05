import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";
import { getAccessToken, onSessionExpired, refreshSession, setAccessToken } from "./auth";
import { safeNext } from "../pages/LoginPage";

// Route requests through a fake adapter: handler(config) -> {status, data}.
function useAdapter(handler) {
  const calls = [];
  api.defaults.adapter = async (config) => {
    calls.push({ url: config.url, auth: config.headers?.Authorization ?? null });
    const { status, data } = await handler(config);
    const response = { data, status, statusText: String(status), headers: {}, config, request: {} };
    if (status >= 400) {
      const err = new Error(`HTTP ${status}`);
      err.config = config;
      err.response = response;
      throw err;
    }
    return response;
  };
  return calls;
}

afterEach(() => {
  delete api.defaults.adapter;
  setAccessToken(null);
});

describe("session handling", () => {
  it("keeps the access token in memory only", () => {
    globalThis.localStorage = { setItem: vi.fn(), getItem: vi.fn() };
    try {
      setAccessToken("abc");
      expect(getAccessToken()).toBe("abc");
      expect(globalThis.localStorage.setItem).not.toHaveBeenCalled();
    } finally {
      delete globalThis.localStorage;
    }
  });

  it("shares one refresh request between concurrent callers", async () => {
    const calls = useAdapter(async () => ({ status: 200, data: { access_token: "fresh" } }));
    const [a, b] = await Promise.all([refreshSession(), refreshSession()]);
    expect(a).toBe("fresh");
    expect(b).toBe("fresh");
    expect(calls.filter((c) => c.url === "/api/auth/refresh")).toHaveLength(1);
  });

  it("refreshes once on 401 and retries with the new token", async () => {
    setAccessToken("stale");
    const calls = useAdapter(async (config) => {
      if (config.url === "/api/auth/refresh") return { status: 200, data: { access_token: "fresh" } };
      return config.headers?.Authorization === "Bearer fresh"
        ? { status: 200, data: { ok: true } }
        : { status: 401, data: { detail: "unauthorized" } };
    });
    const { data } = await api.get("/api/forecast/AAPL");
    expect(data).toEqual({ ok: true });
    expect(calls.map((c) => c.url)).toEqual(["/api/forecast/AAPL", "/api/auth/refresh", "/api/forecast/AAPL"]);
  });

  it("signals session expiry when the refresh fails", async () => {
    setAccessToken("stale");
    useAdapter(async () => ({ status: 401, data: { detail: "unauthorized" } }));
    const expired = vi.fn();
    const off = onSessionExpired(expired);
    try {
      await expect(api.get("/api/forecast/AAPL")).rejects.toThrow();
      expect(expired).toHaveBeenCalledTimes(1);
      expect(getAccessToken()).toBeNull();
    } finally {
      off();
    }
  });
});

describe("post-login redirect", () => {
  it("only allows same-app paths", () => {
    expect(safeNext("?next=%2Fsecurity%2FAAPL")).toBe("/security/AAPL");
    expect(safeNext("?next=https://evil.example")).toBe("/app");
    expect(safeNext("?next=//evil.example")).toBe("/app");
    expect(safeNext("")).toBe("/app");
  });
});
