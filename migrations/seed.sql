-- OneMarket Analyzer — Supabase seed (registry parity).
--
-- Smoke minimum (covered by test_seed_has_three_smoke_instruments):
--   XNAS-AAPL    (US / yfinance)   — Apple Inc.
--   XSHG-600519  (CN / akshare)    — Kweichow Moutai
--   XPAR-MC      (EU / Euronext)   — LVMH
-- Full seed mirrors backend/instruments/registry.py SEED_INSTRUMENTS so a
-- fresh Supabase DB is warm for the screener/liquidity/index fan-outs
-- (cold empty price_bars + live 2y Yahoo fetch from serverless = 502).
-- Bars themselves still require `scripts/backfill_bars.py` or the nightly
-- /api/cron/ingest run — this file seeds INSTRUMENTS only, never prices.
--
-- Apply after supabase/migrations/0001_onemarket.sql (then 0002-0006 in order):
--   psql "$DATABASE_URL" -f supabase/seed.sql
-- Idempotent: ON CONFLICT (exchange_mic, exchange_symbol) DO NOTHING.

INSERT INTO instruments
  (exchange_mic, exchange_symbol, provider_symbol, isin, company_name,
   currency, country, sector, timezone, trading_calendar, is_active)
VALUES
  ('XNAS', 'AAPL', 'AAPL', 'US0378331005', 'Apple Inc.',
   'USD', 'US', 'Technology', 'America/New_York', 'XNAS', TRUE),
  ('XNAS', 'MSFT', 'MSFT', 'US5949181045', 'Microsoft Corporation',
   'USD', 'US', 'Technology', 'America/New_York', 'XNAS', TRUE),
  ('XNAS', 'NVDA', 'NVDA', 'US67066G1040', 'NVIDIA Corporation',
   'USD', 'US', 'Technology', 'America/New_York', 'XNAS', TRUE),
  ('XNAS', 'TSLA', 'TSLA', 'US88160R1014', 'Tesla, Inc.',
   'USD', 'US', 'Consumer Cyclical', 'America/New_York', 'XNAS', TRUE),
  ('XNAS', 'GOOGL', 'GOOGL', 'US02079K3059', 'Alphabet Inc.',
   'USD', 'US', 'Technology', 'America/New_York', 'XNAS', TRUE),
  ('XNAS', 'AMZN', 'AMZN', 'US0231351067', 'Amazon.com, Inc.',
   'USD', 'US', 'Consumer Cyclical', 'America/New_York', 'XNAS', TRUE),
  ('XNAS', 'META', 'META', 'US30303M1027', 'Meta Platforms, Inc.',
   'USD', 'US', 'Technology', 'America/New_York', 'XNAS', TRUE),
  ('XNAS', 'AVGO', 'AVGO', 'US11135F1012', 'Broadcom Inc.',
   'USD', 'US', 'Technology', 'America/New_York', 'XNAS', TRUE),
  ('XNYS', 'AAP', 'AAP', 'US00751Y1064', 'Advance Auto Parts, Inc.',
   'USD', 'US', 'Consumer Cyclical', 'America/New_York', 'XNYS', TRUE),
  ('XNYS', 'JPM', 'JPM', 'US46625H1005', 'JPMorgan Chase & Co.',
   'USD', 'US', 'Financial Services', 'America/New_York', 'XNYS', TRUE),
  ('XSHG', '600519', '600519.SS', 'CNE0000018R8', 'Kweichow Moutai Co., Ltd.',
   'CNY', 'CN', 'Consumer Defensive', 'Asia/Shanghai', 'XSHG', TRUE),
  ('XSHG', '600000', '600000.SS', 'CNE000000JP3', 'Shanghai Pudong Development Bank',
   'CNY', 'CN', 'Financial Services', 'Asia/Shanghai', 'XSHG', TRUE),
  ('XSHG', '600036', '600036.SS', 'CNE000001B33', 'China Merchants Bank Co., Ltd.',
   'CNY', 'CN', 'Financial Services', 'Asia/Shanghai', 'XSHG', TRUE),
  ('XSHG', '601318', '601318.SS', 'CNE000001R84', 'Ping An Insurance (Group) Company of China, Ltd.',
   'CNY', 'CN', 'Financial Services', 'Asia/Shanghai', 'XSHG', TRUE),
  ('XSHG', '600900', '600900.SS', 'CNE000001G38', 'China Yangtze Power Co., Ltd.',
   'CNY', 'CN', 'Utilities', 'Asia/Shanghai', 'XSHG', TRUE),
  ('XPAR', 'MC', 'MC.PA', 'FR0000121014', 'LVMH Moet Hennessy Louis Vuitton SE',
   'EUR', 'FR', 'Consumer Cyclical', 'Europe/Paris', 'XPAR', TRUE),
  ('XPAR', 'ACA', 'ACA.PA', 'FR0000045072', 'Credit Agricole S.A.',
   'EUR', 'FR', 'Financial Services', 'Europe/Paris', 'XPAR', TRUE),
  ('XPAR', 'OR', 'OR.PA', 'FR0000120321', 'L''Oreal S.A.',
   'EUR', 'FR', 'Consumer Defensive', 'Europe/Paris', 'XPAR', TRUE),
  ('XAMS', 'ASML', 'ASML.AS', 'NL0010273215', 'ASML Holding N.V.',
   'EUR', 'NL', 'Technology', 'Europe/Amsterdam', 'XAMS', TRUE),
  ('XAMS', 'INGA', 'INGA.AS', 'NL0011821202', 'ING Groep N.V.',
   'EUR', 'NL', 'Financial Services', 'Europe/Amsterdam', 'XAMS', TRUE),
  ('XBRU', 'UCB', 'UCB.BR', 'BE0003739530', 'UCB S.A.',
   'EUR', 'BE', 'Healthcare', 'Europe/Brussels', 'XBRU', TRUE),
  ('XBRU', 'ABI', 'ABI.BR', 'BE0974293251', 'Anheuser-Busch InBev SA/NV',
   'EUR', 'BE', 'Consumer Defensive', 'Europe/Brussels', 'XBRU', TRUE),
  ('XNAS', 'SPY', 'SPY', NULL, 'SPDR S&P 500 ETF Trust',
   'USD', 'US', 'ETF', 'America/New_York', 'XNAS', TRUE),
  ('XNAS', 'QQQ', 'QQQ', NULL, 'Invesco QQQ Trust',
   'USD', 'US', 'ETF', 'America/New_York', 'XNAS', TRUE),
  ('XNAS', 'EWQ', 'EWQ', NULL, 'iShares MSCI France ETF',
   'USD', 'US', 'ETF', 'America/New_York', 'XNAS', TRUE),
  ('XNAS', 'EWN', 'EWN', NULL, 'iShares MSCI Netherlands ETF',
   'USD', 'US', 'ETF', 'America/New_York', 'XNAS', TRUE),
  ('XNAS', 'EWK', 'EWK', NULL, 'iShares MSCI Belgium ETF',
   'USD', 'US', 'ETF', 'America/New_York', 'XNAS', TRUE),
  ('XSHG', '000001', '000001.SS', NULL, 'SSE Composite Index',
   'CNY', 'CN', 'Index', 'Asia/Shanghai', 'XSHG', TRUE),
  ('XPAR', 'CAC', 'CAC.PA', NULL, 'CAC 40 Index',
   'EUR', 'FR', 'Index', 'Europe/Paris', 'XPAR', TRUE),
  ('XAMS', 'IAEX', 'IAEX.AS', NULL, 'iShares AEX UCITS ETF',
   'EUR', 'NL', 'ETF', 'Europe/Amsterdam', 'XAMS', TRUE)
ON CONFLICT (exchange_mic, exchange_symbol) DO NOTHING;
