import { afterEach, describe, expect, it } from "vitest";
import { WATCHLIST_KEY, loadWatchlist, watchlistKey } from "./useWatchlist";

function fakeStorage(initial = {}) {
  const bag = { ...initial };
  globalThis.localStorage = {
    getItem: (k) => (k in bag ? bag[k] : null),
    setItem: (k, v) => { bag[k] = String(v); },
    removeItem: (k) => { delete bag[k]; },
  };
  return bag;
}

afterEach(() => {
  delete globalThis.localStorage;
});

describe("per-user watchlists", () => {
  it("keeps separate lists per account", () => {
    fakeStorage({
      [watchlistKey("alice")]: JSON.stringify(["AAPL"]),
      [watchlistKey("bob")]: JSON.stringify(["MC.PA"]),
    });
    expect(loadWatchlist(watchlistKey("alice"))).toEqual(["AAPL"]);
    expect(loadWatchlist(watchlistKey("bob"))).toEqual(["MC.PA"]);
  });

  it("hands a pre-existing shared list to the first account only", () => {
    const bag = fakeStorage({ [WATCHLIST_KEY]: JSON.stringify(["NVDA", "KO"]) });
    expect(loadWatchlist(watchlistKey("alice"))).toEqual(["NVDA", "KO"]);
    expect(bag[WATCHLIST_KEY]).toBeUndefined();
    expect(loadWatchlist(watchlistKey("bob"))).not.toContain("KO");  // bob gets the default list
  });
});
