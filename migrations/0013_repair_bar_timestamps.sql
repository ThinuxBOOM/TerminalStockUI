-- OneMarket Analyzer — 0013: repair index venues and duplicate daily bars.
--
-- Apply with `python scripts/migrate.py`. Idempotent.
--
-- 1. Benchmark indices (^FCHI, ^AEX, ^BFX, ...) were auto-registered under
--    XNAS with timezone UTC, so their session days were computed on New York
--    time (a day early for Euronext). Move them to their own venue.
-- 2. Auto-registered instruments kept timezone 'UTC'; set the venue's zone.
-- 3. Some providers stamp daily candles at 00:00 UTC while yfinance stamps
--    exchange-local midnight, so a session could be stored twice. Rows at
--    00:00 UTC on non-UTC venues are deleted when the local-midnight row for
--    the same session exists, otherwise re-stamped to local midnight.
--    Deletes duplicate rows only; take a backup first if in doubt.

-- 1 ---------------------------------------------------------------------------
UPDATE instruments i
   SET exchange_mic = v.mic, trading_calendar = v.mic, currency = v.ccy, updated_at = now()
  FROM (VALUES ('^FCHI', 'XPAR', 'EUR'), ('^AEX', 'XAMS', 'EUR'), ('^BFX', 'XBRU', 'EUR'),
               ('^NYA', 'XNYS', 'USD'), ('^GSPC', 'XNYS', 'USD'), ('^DJI', 'XNYS', 'USD'))
       AS v(sym, mic, ccy)
 WHERE i.provider_symbol = v.sym
   AND i.exchange_mic <> v.mic
   AND NOT EXISTS (SELECT 1 FROM instruments j WHERE j.exchange_mic = v.mic AND j.exchange_symbol = i.exchange_symbol);

-- 2 ---------------------------------------------------------------------------
UPDATE instruments
   SET timezone = CASE exchange_mic
         WHEN 'XNYS' THEN 'America/New_York' WHEN 'XNAS' THEN 'America/New_York'
         WHEN 'XSHG' THEN 'Asia/Shanghai'    WHEN 'XPAR' THEN 'Europe/Paris'
         WHEN 'XAMS' THEN 'Europe/Amsterdam' WHEN 'XBRU' THEN 'Europe/Brussels' END,
       updated_at = now()
 WHERE timezone = 'UTC'
   AND exchange_mic IN ('XNYS', 'XNAS', 'XSHG', 'XPAR', 'XAMS', 'XBRU');

-- 3 ---------------------------------------------------------------------------
DELETE FROM price_bars p
 USING instruments i
 WHERE p.instrument_id = i.instrument_id
   AND p.timeframe IN ('1d', '1wk', '1mo')
   AND i.timezone NOT IN ('UTC', 'Etc/UTC')
   AND (p.ts AT TIME ZONE 'UTC')::time = '00:00'
   AND p.ts <> ((p.ts AT TIME ZONE 'UTC')::date::timestamp AT TIME ZONE i.timezone)
   AND EXISTS (
         SELECT 1 FROM price_bars q
          WHERE q.instrument_id = p.instrument_id AND q.timeframe = p.timeframe
            AND q.ts = ((p.ts AT TIME ZONE 'UTC')::date::timestamp AT TIME ZONE i.timezone));

UPDATE price_bars p
   SET ts = ((p.ts AT TIME ZONE 'UTC')::date::timestamp AT TIME ZONE i.timezone)
  FROM instruments i
 WHERE p.instrument_id = i.instrument_id
   AND p.timeframe IN ('1d', '1wk', '1mo')
   AND i.timezone NOT IN ('UTC', 'Etc/UTC')
   AND (p.ts AT TIME ZONE 'UTC')::time = '00:00'
   AND p.ts <> ((p.ts AT TIME ZONE 'UTC')::date::timestamp AT TIME ZONE i.timezone);
