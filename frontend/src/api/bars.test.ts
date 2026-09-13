import { describe, expect, it } from 'vitest';
import {
  normalizeAIHealthTest,
  normalizeBarsToCandles,
  normalizeBarTime,
  type Provenance,
} from './client';

// Honesty-audit specs: bars normalization must never invent candles, and the
// AI health-test fallback must never report a silent pass.
// Pinned timestamp — never Date.now()/new Date() in assertions.
const TS = '2026-01-15T12:00:00.000Z';

function prov(over: Partial<Provenance> = {}): Provenance {
  return {
    source: 'yfinance',
    as_of: TS,
    delay_minutes: 15,
    quality_grade: 'C',
    fallback_used: true,
    missing_fields: [],
    ...over,
  };
}

function bar(ts: string, o: number, h: number, l: number, c: number) {
  return { ts, open: o, high: h, low: l, close: c, volume: 1000, missing_fields: [] };
}

describe('normalizeBarTime', () => {
  it('keeps YYYY-MM-DD and truncates ISO datetimes to the day', () => {
    expect(normalizeBarTime('2026-01-15')).toBe('2026-01-15');
    expect(normalizeBarTime('2026-01-15T00:00:00+00:00')).toBe('2026-01-15');
  });

  it('converts epoch seconds/milliseconds to YYYY-MM-DD', () => {
    expect(normalizeBarTime(1768435200)).toBe('2026-01-15');
    expect(normalizeBarTime(1768435200000)).toBe('2026-01-15');
  });

  it('returns null for unparseable input (never guesses)', () => {
    expect(normalizeBarTime('not-a-date')).toBeNull();
    expect(normalizeBarTime('')).toBeNull();
    expect(normalizeBarTime(null)).toBeNull();
    expect(normalizeBarTime(undefined)).toBeNull();
    expect(normalizeBarTime({})).toBeNull();
  });
});

describe('normalizeBarsToCandles (GET /api/market_data/bars contract)', () => {
  it('extracts candles from the canonical backend shape', () => {
    const out = normalizeBarsToCandles(
      {
        symbol: 'AAPL',
        timeframe: '1d',
        bars: [bar('2026-01-14T00:00:00+00:00', 100, 102, 99, 101), bar('2026-01-15', 101, 103, 100, 102)],
        provenance: prov(),
      },
      'AAPL',
      '1d',
    );
    expect(out.symbol).toBe('AAPL');
    expect(out.timeframe).toBe('1d');
    expect(out.candles).toEqual([
      { time: '2026-01-14', open: 100, high: 102, low: 99, close: 101 },
      { time: '2026-01-15', open: 101, high: 103, low: 100, close: 102 },
    ]);
    expect(out.provenance.source).toBe('yfinance');
    expect(out.provenance.fallback_used).toBe(true);
  });

  it('accepts alias keys (data/candles, time/date) without inventing rows', () => {
    const out = normalizeBarsToCandles(
      {
        ticker: 'MC.PA',
        data: [{ time: '2026-01-15', open: '10', high: 11, low: 9, close: 10.5 }],
        provenance: prov({ source: 'fx-free', fallback_used: false }),
      },
      'MC.PA',
      '1d',
    );
    expect(out.symbol).toBe('MC.PA');
    expect(out.candles).toHaveLength(1);
    expect(out.candles[0]).toEqual({ time: '2026-01-15', open: 10, high: 11, low: 9, close: 10.5 });
  });

  it('drops invalid rows instead of zero-filling', () => {
    const out = normalizeBarsToCandles(
      {
        symbol: 'X',
        bars: [
          bar('2026-01-15', 1, 2, 0.5, 1.5),
          { ts: 'bogus', open: 1, high: 2, low: 0.5, close: 1.5 },
          { ts: '2026-01-14', open: null, high: 2, low: 0.5, close: 1.5 },
          { ts: '2026-01-13', open: 1, high: 'NaN', low: 0.5, close: 1.5 },
          'junk-row',
          null,
        ],
        provenance: prov(),
      },
      'X',
    );
    expect(out.candles).toEqual([{ time: '2026-01-15', open: 1, high: 2, low: 0.5, close: 1.5 }]);
  });

  it('yields zero candles with stale-marked provenance on empty/unparseable payloads', () => {
    // Empty bars with a valid backend envelope: zero candles, envelope kept.
    const empty = normalizeBarsToCandles({ symbol: 'X', bars: [], provenance: prov() }, 'X');
    expect(empty.candles).toEqual([]);
    expect(empty.provenance.fallback_used).toBe(true);
    // Envelope absent entirely: stale-marked fallback with missing_fields flagged.
    for (const raw of [{ symbol: 'X', bars: [] }, {}, null, undefined]) {
      const out = normalizeBarsToCandles(raw, 'X');
      expect(out.candles).toEqual([]);
      expect(out.symbol).toBe('X');
      expect(out.provenance.fallback_used).toBe(true);
      expect(out.provenance.missing_fields).toContain('provenance');
    }
  });
});

describe('normalizeAIHealthTest (POST /api/ai/providers/health/test contract)', () => {
  it('passes {ok} shapes through', () => {
    expect(normalizeAIHealthTest({ ok: true, latency_ms: 12, message: 'probe recorded' }, 'gemini')).toEqual({
      ok: true,
      latency_ms: 12,
      message: 'probe recorded',
      provider: 'gemini',
    });
  });

  it('maps configured=false to FAIL with an honest key-missing message (never silent pass)', () => {
    const out = normalizeAIHealthTest(
      { providers: [{ provider: 'gemini', model: 'gemini-3.7-flash', configured: false, stub_mode: true }] },
      'gemini',
    );
    expect(out?.ok).toBe(false);
    expect(out?.message).toContain('API key missing');
    expect(out?.provider).toBe('gemini');
  });

  it('maps configured=true to OK and names the model', () => {
    const out = normalizeAIHealthTest(
      { providers: [{ provider: 'openai', model: 'gpt-x', configured: true, stub_mode: false }] },
      'openai',
    );
    expect(out?.ok).toBe(true);
    expect(out?.message).toContain('gpt-x');
  });

  it('returns null when the payload carries no recognizable shape', () => {
    expect(normalizeAIHealthTest({}, 'gemini')).toBeNull();
    expect(normalizeAIHealthTest({ providers: [] }, 'gemini')).toBeNull();
    expect(normalizeAIHealthTest(null, 'gemini')).toBeNull();
  });
});
