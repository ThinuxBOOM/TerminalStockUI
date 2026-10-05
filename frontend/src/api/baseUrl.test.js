import { describe, expect, it } from "vitest";
import { normalizeHttpUrl, resolveApiBaseUrl } from "./baseUrl";

describe("baseUrl", () => {
  it("defaults to same-origin", () => {
    expect(resolveApiBaseUrl()).toBe("");
  });

  it("accepts only http(s) URLs and strips trailing slashes", () => {
    expect(normalizeHttpUrl("https://api.example.com/")).toBe("https://api.example.com");
    expect(normalizeHttpUrl("javascript:alert(1)")).toBeNull();
    expect(normalizeHttpUrl("//evil.example")).toBeNull();
    expect(normalizeHttpUrl("")).toBeNull();
  });

  it("ignores ?api= and localStorage overrides", () => {
    globalThis.window = { location: { search: "?api=https://evil.example" } };
    globalThis.localStorage = { getItem: () => "https://evil.example" };
    try {
      expect(resolveApiBaseUrl()).toBe("");
    } finally {
      delete globalThis.window;
      delete globalThis.localStorage;
    }
  });
});
