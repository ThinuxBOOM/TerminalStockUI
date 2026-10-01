import { describe, expect, it } from "vitest";
import { coalesceInflight } from "./client";

function canceled() {
  const err = new Error("canceled");
  err.name = "CanceledError";
  err.code = "ERR_CANCELED";
  return err;
}

describe("coalesceInflight", () => {
  it("shares one request between concurrent callers", async () => {
    let calls = 0;
    const fn = () => { calls += 1; return Promise.resolve("ok"); };
    const [a, b] = await Promise.all([coalesceInflight("t:share", fn), coalesceInflight("t:share", fn)]);
    expect([a, b, calls]).toEqual(["ok", "ok", 1]);
  });
  it("re-runs a joiner's own request when the first caller aborts", async () => {
    const leader = coalesceInflight("t:abort", () => Promise.reject(canceled()));
    const joiner = coalesceInflight("t:abort", () => Promise.resolve("joiner data"));
    await expect(leader).rejects.toMatchObject({ code: "ERR_CANCELED" });
    await expect(joiner).resolves.toBe("joiner data");
  });
  it("still shares real failures", async () => {
    const boom = new Error("502");
    const leader = coalesceInflight("t:fail", () => Promise.reject(boom));
    const joiner = coalesceInflight("t:fail", () => Promise.resolve("never"));
    await expect(leader).rejects.toBe(boom);
    await expect(joiner).rejects.toBe(boom);
  });
});
