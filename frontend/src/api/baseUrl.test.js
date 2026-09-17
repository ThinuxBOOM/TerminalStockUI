import { beforeEach, describe, expect, it } from "vitest";
import {
  API_BASE_OVERRIDE_KEY,
  clearApiBaseUrlOverride,
  normalizeHttpUrl,
  resolveApiBaseUrl,
  resolveApiBaseUrlWithSource,
  setApiBaseUrlOverride,
} from "./baseUrl";

function stubBrowser({ search = "", stored = null, runtime = undefined } = {}) {
  const bag = stored === null ? {} : { [API_BASE_OVERRIDE_KEY]: stored };
  globalThis.window = {
    location: { protocol: "http:", hostname: "localhost", search },
    __ONEMARKET_CONFIG__: runtime,
  };
  globalThis.localStorage = {
    getItem: (k) => (k in bag ? bag[k] : null),
    setItem: (k, v) => {
      bag[k] = String(v);
    },
    removeItem: (k) => {
      delete bag[k];
    },
    __bag: bag,
  };
  return bag;
}

beforeEach(() => {
  delete globalThis.window;
  delete globalThis.localStorage;
});

describe("normalizeHttpUrl", () => {
  it("accepts http(s) and strips trailing slashes", () => {
    expect(normalizeHttpUrl("http://localhost:8000")).toBe("http://localhost:8000");
    expect(normalizeHttpUrl("  https://api.example.com/  ")).toBe("https://api.example.com");
  });
  it("rejects empty, non-http, and malformed values", () => {
    expect(normalizeHttpUrl("")).toBeNull();
    expect(normalizeHttpUrl("   ")).toBeNull();
    expect(normalizeHttpUrl("ftp://x")).toBeNull();
    expect(normalizeHttpUrl("notaurl")).toBeNull();
    expect(normalizeHttpUrl("http://exa mple.com")).toBeNull();
    expect(normalizeHttpUrl(null)).toBeNull();
    expect(normalizeHttpUrl(undefined)).toBeNull();
    expect(normalizeHttpUrl(123)).toBeNull();
  });
});

describe("resolveApiBaseUrl (Node default contract)", () => {
  it("targets local FastAPI when no browser and no build-time value", () => {
    expect(resolveApiBaseUrl()).toBe("http://localhost:8000");
    expect(resolveApiBaseUrlWithSource().source).toBe("default");
  });
});

describe("browser override layers", () => {
  it("runtime config.js wins over the default (no rebuild needed)", () => {
    stubBrowser({ runtime: { API_BASE_URL: "http://192.168.1.20:8000" } });
    expect(resolveApiBaseUrlWithSource()).toEqual({
      url: "http://192.168.1.20:8000",
      source: "runtime-config",
    });
  });
  it("localStorage override wins over runtime config", () => {
    stubBrowser({
      stored: "http://other-host:9000",
      runtime: { API_BASE_URL: "http://192.168.1.20:8000" },
    });
    expect(resolveApiBaseUrlWithSource()).toEqual({
      url: "http://other-host:9000",
      source: "override",
    });
  });
  it("?api= persists to storage and wins immediately", () => {
    const bag = stubBrowser({ search: "?api=http://query-host:8000/" });
    expect(resolveApiBaseUrlWithSource()).toEqual({
      url: "http://query-host:8000",
      source: "query",
    });
    expect(bag[API_BASE_OVERRIDE_KEY]).toBe("http://query-host:8000");
  });
  it("?api=clear drops a stored override", () => {
    stubBrowser({ search: "?api=clear", stored: "http://other-host:9000" });
    const { source, url } = resolveApiBaseUrlWithSource();
    expect(source).not.toBe("override");
    expect(url).not.toBe("http://other-host:9000");
  });
  it("invalid layers fall through instead of breaking", () => {
    stubBrowser({ stored: "ftp://nope", runtime: { API_BASE_URL: "garbage" } });
    expect(resolveApiBaseUrl()).toBe("http://localhost:8000");
  });
});

describe("override helpers", () => {
  it("set/clear round-trips valid URLs and rejects the rest", () => {
    stubBrowser({});
    expect(setApiBaseUrlOverride("http://lan:8000/")).toBe("http://lan:8000");
    expect(resolveApiBaseUrl()).toBe("http://lan:8000");
    clearApiBaseUrlOverride();
    expect(resolveApiBaseUrl()).toBe("http://localhost:8000");
    expect(() => setApiBaseUrlOverride("notaurl")).toThrow();
  });
});
