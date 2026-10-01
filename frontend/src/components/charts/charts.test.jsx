import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FanChart } from "./FanChart";
import { RangeBar, RankMeter, Sparkline } from "./Inline";
import { BarList, Heatmap } from "./Bars";

const text = (el) => renderToStaticMarkup(el).replace(/<[^>]+>/g, " ");

describe("inline charts", () => {
  it("render an em dash instead of inventing values", () => {
    expect(text(<RangeBar low={null} high={0.1} />)).toContain("—");
    expect(text(<RankMeter value={undefined} />)).toContain("—");
    expect(text(<Sparkline values={[1]} />)).toContain("—");
  });
  it("label a range with signed percentages", () => {
    expect(text(<RangeBar low={-0.081} mid={0.01} high={0.094} showLabels />)).toMatch(/−8\.1%.*\+9\.4%/);
  });
});

describe("FanChart", () => {
  const q = (s) => ({ "0.05": -1.6 * s, "0.10": -1.3 * s, "0.25": -0.7 * s, "0.50": 0.002, "0.75": 0.7 * s, "0.90": 1.3 * s, "0.95": 1.6 * s });
  it("draws a band per horizon with today's anchor and price axis", () => {
    const html = renderToStaticMarkup(<FanChart horizons={{ 1: { quantiles: q(0.02) }, 21: { quantiles: q(0.08) } }} lastClose={100} currency="USD" />);
    expect(html).toContain("Today");
    expect(html).toContain(">21d<");
    expect(html).toContain("$100");
    expect((html.match(/<path/g) || []).length).toBeGreaterThanOrEqual(4);
  });
  it("says so when there is nothing to draw", () => {
    expect(text(<FanChart horizons={{}} />)).toContain("unavailable");
  });
});

describe("bars and heatmap", () => {
  it("lists shares without a plus sign", () => {
    expect(text(<BarList data={[{ label: "AAPL", value: 0.25 }]} />)).toContain("25.0%");
  });
  it("prints a clean zero correlation", () => {
    expect(text(<Heatmap labels={["A", "B"]} matrix={[[1, -0.001], [-0.001, 1]]} />)).not.toContain("-0.00");
  });
});
