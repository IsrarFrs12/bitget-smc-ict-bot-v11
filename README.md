# Bitget SMC/ICT V10 — Regime + S/R Mature

V10 is a DEMO-first, rules-based execution engine. It combines confirmed-candle SMC/ICT analysis with an independent horizontal support/resistance rejection model.

## V10 hardening
- Conservative S/R scoring using touches, clean reactions, clustering, proximity, recency and break penalties.
- Strong S/R requires minimum score, multiple touches, at least one directional reaction, and no more than one break.
- SMC premium/discount gating remains strict.
- S/R rejection is a first-class setup family and can be evaluated independently of P/D, while still requiring HTF directional bias plus 15m rejection/structure confirmation.
- RR < minimum is always rejected.
- Swing prices are normalized before target selection.
- Explainable state/rejection reporting is retained.
- Demo mode and live-trading safety locks remain unchanged.

## Safety
Backtest and forward-test before any live use. No strategy guarantees profit.

## Run
```powershell
python -u main.py
```

## V11 maturity layer

### Price plan by timeframe
- **1D + 4H:** directional bias only.
- **1H:** `PROJECTED` plan — entry, SL, TP and RR derived from the 1H POI/S/R and confirmed swing targets.
- **15M:** `CONFIRMED` plan — entry/SL/TP are only execution-ready after the 15m trigger chain passes.

The report now prints both plans so a setup can be watched before the execution session.

### Empirical win accuracy
The bot reports **historical win rate**, sample size, profit factor and expectancy from closed journal results. It does **not** invent a future win probability. Until enough closed samples exist, the win-rate field remains `-`.

### Execution-state hardening
- A limit entry is `PENDING` until the exchange reports a fill.
- Filled/partially-filled entries are reconciled before the position is treated as open.
- SL/TP protection is attempted only after a fill.
- Closed exchange positions are reconciled against exchange fills and sent through `RiskManager.register_result()`.
- Trade IDs are persisted for entry/exit pairing.
- Failed exchange position queries no longer look like a genuine zero-position state.

### Backtest realism
The backtester now waits for price to actually touch the proposed limit entry. A signal that never fills is not counted as a trade. Same-candle SL/TP ambiguity is handled conservatively as SL-first.

Run:
```powershell
python -u backtest.py data/BTC_USDT_15m.csv
```
