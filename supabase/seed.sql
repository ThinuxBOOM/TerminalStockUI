-- OneMarket Analyzer — Supabase minimal seed (smoke test).
--
-- Three canonical instruments covering the three supported quote surfaces:
--   XNAS-AAPL    (US / yfinance)   — Apple Inc.
--   XSHG-600519  (CN / akshare)    — Kweichow Moutai
--   XPAR-MC      (EU / Euronext)   — LVMH
--
-- Apply after supabase/migrations/0001_onemarket.sql:
--   psql "$DATABASE_URL" -f supabase/seed.sql
-- Idempotent: ON CONFLICT (exchange_mic, exchange_symbol) DO NOTHING.

INSERT INTO instruments
  (exchange_mic, exchange_symbol, provider_symbol, isin, company_name,
   currency, country, sector, timezone, trading_calendar, is_active)
VALUES
  ('XNAS', 'AAPL', 'AAPL', 'US0378331005', 'Apple Inc.',
   'USD', 'US', 'Technology', 'America/New_York', 'XNAS', TRUE),
  ('XSHG', '600519', '600519.SS', 'CNE0000018R8', 'Kweichow Moutai Co., Ltd.',
   'CNY', 'CN', 'Consumer Staples', 'Asia/Shanghai', 'XSHG', TRUE),
  ('XPAR', 'MC', 'MC.PA', 'FR0000121014', 'LVMH Moet Hennessy Louis Vuitton SE',
   'EUR', 'FR', 'Consumer Cyclical', 'Europe/Paris', 'XPAR', TRUE)
ON CONFLICT (exchange_mic, exchange_symbol) DO NOTHING;
