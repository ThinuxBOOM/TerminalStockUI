import { describe, expect, it } from "vitest";
import { portfolioKey, sanitize } from "./usePortfolio";

describe("usePortfolio sanitize", () => {
  it("uppercases, dedupes and drops invalid rows", () => {
    expect(sanitize([{ symbol: " aapl ", amount: 100 }, { symbol: "AAPL", amount: 5 }, { symbol: "", amount: 1 }, { symbol: "MSFT", amount: "x" }]))
      .toEqual([{ symbol: "AAPL", amount: 100 }]);
    expect(sanitize(null)).toEqual([]);
  });
  it("keys storage per user", () => {
    expect(portfolioKey("u1")).not.toBe(portfolioKey("u2"));
  });
});
