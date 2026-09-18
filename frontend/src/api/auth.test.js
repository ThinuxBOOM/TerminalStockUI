import { afterEach, describe, expect, it } from "vitest";
import {
  authHeaderFor,
  clearTokens,
  extractToken,
  getAccessToken,
  normalizeMe,
  setAccessToken,
  setupAuthInterceptor,
} from "./auth";

afterEach(() => {
  clearTokens();
});

describe("auth token storage (memory + localStorage fallback, node-safe)", () => {
  it("round-trips a token without throwing (memory fallback in node)", () => {
    expect(getAccessToken()).toBeNull();
    setAccessToken("abc123");
    expect(getAccessToken()).toBe("abc123");
    clearTokens();
    expect(getAccessToken()).toBeNull();
  });

  it("trims tokens and treats blank as cleared", () => {
    setAccessToken("  xyz  ");
    expect(getAccessToken()).toBe("xyz");
    setAccessToken("   ");
    expect(getAccessToken()).toBeNull();
  });

  it("authHeaderFor builds Bearer headers, empty object when blank", () => {
    expect(authHeaderFor("tok")).toEqual({ Authorization: "Bearer tok" });
    expect(authHeaderFor("  ")).toEqual({});
    expect(authHeaderFor(null)).toEqual({});
    expect(authHeaderFor(undefined)).toEqual({});
  });
});

describe("auth payload helpers", () => {
  it("extractToken reads access_token / accessToken / token shapes", () => {
    expect(extractToken({ access_token: "a" })).toBe("a");
    expect(extractToken({ accessToken: "b" })).toBe("b");
    expect(extractToken({ token: "c" })).toBe("c");
    expect(extractToken({})).toBeNull();
    expect(extractToken(null)).toBeNull();
  });

  it("normalizeMe defaults tier to free and coerces admin flag", () => {
    expect(normalizeMe({})).toMatchObject({ tier: "free", is_admin: false });
    expect(
      normalizeMe({ id: "1", email: "a@b.c", tier: "gold", is_admin: true })
    ).toMatchObject({ id: "1", email: "a@b.c", tier: "gold", is_admin: true });
  });

  it("setupAuthInterceptor wires providers without throwing", () => {
    expect(() => setupAuthInterceptor()).not.toThrow();
  });
});
