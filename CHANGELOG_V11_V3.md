# V11 V3 — public ticker timeout resilience

## Fixed
- Bulk `fetch_tickers()` timeout no longer bubbles out of `run_once()` and kills a scan cycle.
- Public market-data client gets a dedicated 30s default timeout via `MARKET_TICKER_TIMEOUT_MS`.
- Bulk ticker fetch has independent retries with short backoff.
- After a successful liquidity scan, the selected liquid universe is cached in `state/liquid_universe.json`.
- If Bitget's bulk ticker endpoint is temporarily unavailable on a later cycle, the bot uses the recent cached liquid universe instead of crashing/skipping the entire strategy pipeline.
- Cache expires after `TICKER_CACHE_TTL_SECONDS` (default 30 minutes), so stale symbols are not retained indefinitely.

## Safety behavior
- If ticker fetch fails and no valid cache exists, the bot performs a clean no-op for that scan.
- It does not invent volume, spread, or liquidity values.
