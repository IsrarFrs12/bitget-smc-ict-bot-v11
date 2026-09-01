# V11 Mature Layer — Changes

1. Timeframe price plan
   - 1D/4H remain directional bias.
   - 1H produces a PROJECTED Entry/SL/TP/RR from the active POI/S/R.
   - 15M produces the CONFIRMED execution Entry/SL/TP/RR only after triggers pass.

2. Historical accuracy
   - Added stats_engine.py.
   - Reports empirical win rate, sample size, profit factor and expectancy from CLOSED journal rows.
   - No synthetic/predicted win percentage is generated.

3. Execution lifecycle
   - Limit entries are PENDING until filled.
   - Pending orders are checked each scan.
   - Filled entries get protection before being marked open.
   - Closed exchange positions are reconciled from exchange fills and call RiskManager.register_result().
   - Trade IDs are stored for entry/exit pairing.

4. Risk-state safety
   - Failed position queries no longer get interpreted as zero open positions.
   - Equity lookup retries both unified balance and the Bitget account endpoint.

5. Backtest
   - A limit signal must actually touch the entry before it counts as a trade.
   - Entry timeout and max holding bars are configurable.
   - Same-bar SL/TP ambiguity is handled conservatively as SL-first.

Important:
- Protection order parameter compatibility depends on the installed CCXT/Bitget API version.
- Run in DEMO first and verify actual Bitget order status/protection behavior.
- Historical win rate is descriptive, not a guarantee of future performance.
