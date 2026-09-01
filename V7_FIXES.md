# V7 Fixes

1. Session filtering is execution-only: HTF/1H LONG/SHORT setups remain visible outside London/New York.
2. Outside session, 15m trigger API calls are skipped and setups remain on the watchlist.
3. `analyze_symbol()` has an explicit session safety check, so removing the setup gate cannot create off-session orders.
4. Explainable reporter upserts by symbol, avoiding duplicate setup/trigger rows.
5. Reports preserve the final state and exact reason for each coin.
