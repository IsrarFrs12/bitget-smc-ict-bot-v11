from services.telegram_notifier import telegram
# V6 explainable reporting module
try:
    from decision_reporter import ExplainableReporter, CoinDecision
    REPORTING_AVAILABLE = True
except Exception:
    REPORTING_AVAILABLE = False

import time
import traceback
import uuid
import pandas as pd
from datetime import datetime, timezone

from config import Config
from executor import BitgetExecutor
from scanner import get_tradable_symbols, fetch_stage
from smc_engine import market_structure, displacement, liquidity_sweep, session_allowed, session_name, atr, swing_prices, price_action_confirmation
from strategy import analyze_symbol, setup_snapshot
from watchlist import Watchlist
from risk_manager import RiskManager
from journal import log
from stats_engine import historical_stats


def banner():
    print("=" * 72)
    print(" Bitget SMC/ICT V6 — explainable staged SMC/ICT execution engine")
    print("=" * 72)
    print(f"Mode={('DEMO' if Config.DEMO_MODE else 'REAL')} | Risk={Config.RISK_PER_TRADE_PERCENT}% | RR>=1:{Config.MIN_RISK_REWARD}")
    print(f"Confluence>={Config.MIN_CONFLUENCE_SCORE} | Max trades={Config.MAX_CONCURRENT_TRADES} | Daily stop={Config.MAX_DAILY_LOSS_PERCENT}%")
    print("No strategy guarantees profit. Backtest + forward-test before real money.")


def _to_df(raw):
    if raw is None or len(raw) < 20:
        return None
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df.timestamp, unit="ms", utc=True)
    return df.iloc[:-1].reset_index(drop=True)


def _htf_aligned(d, h4):
    if d is None or h4 is None:
        return False
    ds, hs = market_structure(d), market_structure(h4)
    if ds["bias"] == hs["bias"] and ds["bias"] in ("bullish", "bearish"):
        return True
    # Allow one neutral timeframe only when the other has a clear directional bias.
    if ds["bias"] in ("bullish", "bearish") and hs["bias"] == "neutral":
        return True
    return False


def _setup_candidate(h4, h1):
    if h4 is None or h1 is None:
        return False
    h4s, h1s = market_structure(h4), market_structure(h1)
    if h4s["bias"] not in ("bullish", "bearish"):
        return False
    side = h4s["bias"]
    # The setup timeframe should not directly contradict HTF structure.
    if h1s["bias"] not in (side, "neutral"):
        return False
    pd_zone = h1s.get("premium_discount")
    if side == "bullish" and pd_zone == "premium":
        return False
    if side == "bearish" and pd_zone == "discount":
        return False
    return True


def _reconcile_closed_positions(executor, risk, equity):
    """Reconcile tracked positions that disappeared from the exchange and register one result."""
    raw_positions=executor.fetch_open_positions()
    if raw_positions is None:
        print("[RECON] Exchange position query failed; no closes will be inferred this cycle")
        return
    current = {str(p.get("symbol")): p for p in raw_positions}
    for symbol, tracked in list(risk.open_positions.items()):
        if symbol in current:
            continue
        opened_at=tracked.get("opened_at_ms")
        trades=executor.fetch_my_trades_since(symbol, opened_at, 200)
        exit_side="sell" if tracked.get("side")=="long" else "buy"
        exits=[t for t in trades if str(t.get("side","")).lower()==exit_side]
        qty=float(tracked.get("amount") or tracked.get("position_size") or 0)
        if not exits or qty<=0:
            print(f"[RECON] {symbol}: position closed but exit fill could not be reconstructed yet")
            continue
        remaining=qty; value=fees=0.0
        for t in sorted(exits,key=lambda x:x.get("timestamp") or 0):
            amount=float(t.get("amount") or 0)
            if amount<=0: continue
            used=min(remaining,amount)
            price=float(t.get("price") or 0)
            value += used*price
            fee=t.get("fee") or {}
            fees += float(fee.get("cost") or 0)
            remaining -= used
            if remaining<=1e-12: break
        closed_qty=qty-remaining
        if closed_qty<=0: continue
        exit_avg=value/closed_qty
        entry=float(tracked.get("entry") or 0)
        gross=(exit_avg-entry)*closed_qty if tracked.get("side")=="long" else (entry-exit_avg)*closed_qty
        pnl=gross-fees
        duration_min=""
        if opened_at:
            duration_min=max(0,(pd.Timestamp.now(tz="UTC").timestamp()*1000-float(opened_at))/60000)
        log("EXIT",trade_id=tracked.get("trade_id",""),time=datetime.now(timezone.utc).isoformat(),
            symbol=symbol,side=tracked.get("side",""),model=tracked.get("model",""),
            session=tracked.get("session",""),timeframe=tracked.get("timeframe","15m"),
            entry=entry,exit_price=round(exit_avg,8),sl=tracked.get("sl",""),tp=tracked.get("tp",""),
            rr=tracked.get("rr",""),confluence=tracked.get("confluence",""),
            risk_usdt=tracked.get("risk_usdt",""),notional_usdt=tracked.get("notional",0),
            status="CLOSED",pnl_usdt=round(pnl,8),exit_reason="exchange_position_closed",
            duration_min=round(duration_min,2) if duration_min!="" else "",
            reason="Exit reconciliation from exchange fills")
        risk.register_result(pnl,equity)
        risk.closed(symbol)
        print(f"[RECON] {symbol}: CLOSED | pnl={pnl:.4f} USDT | exit={exit_avg:.8f}")

def _sync_pending_orders(executor, risk):
    """Turn filled/partially-filled pending limits into protected tracked positions."""
    for symbol, pending in list(risk.pending_orders.items()):
        order_id=pending.get("order_id")
        status=executor.fetch_order_status(order_id,symbol) if order_id else None
        if not status:
            continue
        state=str(status.get("status","")).lower()
        filled=float(status.get("filled") or 0)
        if state in ("canceled","cancelled","expired","rejected"):
            risk.remove_pending(symbol)
            print(f"[ORDER] {symbol}: pending entry {state}; removed")
            continue
        if state=="closed" or filled>0:
            if state!="closed" and filled>0:
                executor.cancel_order(order_id,symbol)
                print(f"[ORDER] {symbol}: partial fill {filled}; remaining entry canceled")
            if filled<=0: filled=float(pending.get("amount") or 0)
            protections=executor.place_protection(symbol,pending["side"],filled,pending["sl"],pending["tp"])
            if len(protections)>=2:
                risk.opened(symbol,{**pending,"amount":filled,"position_size":filled,
                                    "opened_at_ms":status.get("timestamp") or pending.get("submitted_at_ms")})
                print(f"[ORDER] {symbol}: ENTRY FILLED | amount={filled} | protection={len(protections)}/2")
            else:
                print(f"[CRITICAL] {symbol}: protection incomplete; position remains risk-blocked")

def _sync_positions(executor, risk, equity):
    _reconcile_closed_positions(executor,risk,equity)
    _sync_pending_orders(executor,risk)
    positions = executor.fetch_open_positions()
    if positions is None:
        print("[RISK] Exchange positions unavailable; preserving local risk state")
        return
    current={}
    for p in positions:
        symbol=str(p.get("symbol"))
        existing=risk.open_positions.get(symbol,{})
        amount=abs(float(p.get("contracts") or p.get("amount") or existing.get("amount") or 0))
        entry=float(p.get("entryPrice") or existing.get("entry") or 0)
        current[symbol]={**existing,"amount":amount,"position_size":amount,
            "notional":abs(float(p.get("notional") or existing.get("notional") or 0)),
            "correlation_group":"crypto","entry":entry,
            "side":existing.get("side") or ("long" if str(p.get("side","")).lower()=="long" else "short"),
            "sl":existing.get("sl",""),"tp":existing.get("tp","")}
    risk.open_positions=current
    risk._save()
    print(f"[RISK] Exchange positions synced: {len(positions)}")


def _decision_from_snapshot(symbol, snap, m15=None, signal=None):
    """Convert real strategy/trigger state into a human-readable CoinDecision."""
    d = CoinDecision(symbol=symbol) if REPORTING_AVAILABLE else None
    if not d:
        return None
    checks = snap.get("checks", {}) or {}
    side = snap.get("side")
    d.direction = "LONG" if side == "bullish" else "SHORT" if side == "bearish" else "NEUTRAL"
    d.bias = str(snap.get("bias") or "UNKNOWN").upper()
    d.setup = "LONG" if side == "bullish" else "SHORT" if side == "bearish" else "NONE"
    d.structure = str(checks.get("h1_structure") or "NONE").upper()
    d.liquidity = str(checks.get("liquidity") or checks.get("liquidity_type") or "NONE").upper()
    d.sweep = str(checks.get("sweep") if "sweep" in checks else "WAITING").upper()
    d.mss_bos = str(checks.get("mss_bos") or checks.get("mss") or checks.get("bos") or "WAITING").upper()
    d.displacement = str(checks.get("displacement") if "displacement" in checks else "WAITING").upper()
    d.fvg = "VALID" if checks.get("fvg") else "NONE"
    d.order_block = "VALID" if checks.get("order_block") else "NONE"
    d.sr_level = "STRONG" if checks.get("sr_level") else "NONE"
    d.sr_score = checks.get("sr_score")
    d.rejection = "YES" if checks.get("sr_rejection") else "WAITING"
    d.volume_confirmation = "YES" if checks.get("volume_confirmed") else "NO"
    d.premium_discount = str(checks.get("h1_pd") or "UNKNOWN").upper()
    d.session = str(snap.get("session") or "UNKNOWN").upper()
    d.state = str(snap.get("status") or "SCANNED").upper()
    d.reason = str(snap.get("reason") or "")
    plan = snap.get("projected_plan") or {}
    d.projected_timeframe = str(plan.get("timeframe","1H"))
    d.projected_entry = plan.get("entry")
    d.projected_stop_loss = plan.get("stop_loss")
    d.projected_take_profit = plan.get("take_profit")
    d.projected_rr = plan.get("rr")
    stats = historical_stats(model=(signal.model if signal else None),
                             side=("long" if side=="bullish" else "short") if side else None,
                             session=(snap.get("session") or None))
    d.historical_win_rate_pct = stats.get("win_rate_pct")
    d.historical_sample_size = stats.get("sample_size",0)
    d.historical_profit_factor = stats.get("profit_factor")
    d.historical_expectancy_usdt = stats.get("expectancy_usdt")
    if signal:
        d.action = "ENTRY READY"
        d.entry, d.stop_loss, d.take_profit = signal.entry, signal.stop_loss, signal.take_profit
        d.rr, d.score = signal.risk_reward, signal.confluence
        md = signal.metadata or {}
        d.liquidity = "SELL-SIDE" if side == "bullish" else "BUY-SIDE"
        d.sweep = "YES" if md.get("sweep") else "NO"
        d.displacement = "YES" if md.get("displacement") else "NO"
        d.sr_level = "STRONG" if md.get("sr_zone") else "NONE"
        d.sr_score = md.get("sr_score")
        d.rejection = "YES" if md.get("sr_rejection") else "NO"
        d.volume_confirmation = "YES" if md.get("volume_confirmed") else "NO"
        d.price_action = ",".join(md.get("price_action",{}).get("patterns",[])) if md.get("price_action") else "NO"
        d.mss_bos = "MSS/CHoCH" if (md.get("mss") or md.get("choch")) else ("BOS" if md.get("bos") else "CONFIRMED")
        d.reason = signal.reason
    return d


def _print_strategy_state(symbol, snap, m15_df=None):
    """Print a compact per-symbol decision even when no order is possible."""
    side = snap.get("side")
    direction = "LONG" if side == "bullish" else "SHORT" if side == "bearish" else "NEUTRAL"
    checks = snap.get("checks", {}) or {}
    status = snap.get("status", "unknown")
    reason = snap.get("reason", "no reason")
    print(f"[COIN] {symbol} | {direction} | bias={snap.get('bias','none')} | state={status}")
    print(
        f"       P/D={checks.get('h1_pd','?')} | OB={'YES' if checks.get('order_block') else 'NO'} "
        f"| FVG={'YES' if checks.get('fvg') else 'NO'} | session={snap.get('session','?')}"
    )
    plan=snap.get("projected_plan") or {}
    if plan:
        print(f"       1H PLAN: Entry={plan.get('entry')} | SL={plan.get('stop_loss')} | TP={plan.get('take_profit')} | RR={plan.get('rr')}")
    if reason:
        print(f"       REASON: {reason}")


def run_once(executor, risk, cycle_no, watchlist):
    started = time.perf_counter()
    reporter = ExplainableReporter() if REPORTING_AVAILABLE else None
    print(f"\n[SCAN] Starting scan cycle #{cycle_no}")

    equity = executor.get_equity_usdt()
    if equity <= 0:
        print("[RISK] Equity unavailable/zero; skipping cycle.")
        return
    risk.update_equity(equity)
    print(f"[RISK] Equity: {equity:.2f} USDT")

    _sync_positions(executor, risk, equity)
    if not risk.trading_allowed(equity):
        print("[RISK] Trading locked by risk manager.")
        return

    symbols = get_tradable_symbols(executor.market_exchange)
    if not symbols:
        print("[SCAN] No liquid symbols.")
        return

    d_raw = fetch_stage(executor.market_exchange, symbols, "1d", Config.DATA_LIMIT, "HTF", Config.SCAN_WORKERS)
    h4_raw = fetch_stage(executor.market_exchange, symbols, "4h", Config.DATA_LIMIT, "HTF", Config.SCAN_WORKERS)

    aligned, d_df, h4_df = [], {}, {}
    for s in symbols:
        d_df[s], h4_df[s] = _to_df(d_raw.get(s)), _to_df(h4_raw.get(s))
        if _htf_aligned(d_df[s], h4_df[s]):
            aligned.append(s)
        else:
            if reporter:
                reporter.add(CoinDecision(
                    symbol=s,
                    direction="NEUTRAL",
                    bias="NEUTRAL",
                    setup="NONE",
                    state="HTF_REJECTED",
                    action="NO ENTRY",
                    reason="1D/4H have no aligned directional bias",
                    strategy_reasons=["HTF bias not aligned"]
                ))

    print(f"[HTF] Directionally aligned: {len(aligned)}/{len(symbols)}")
    if not aligned:
        elapsed=time.perf_counter()-started
        if reporter:
            reporter.save(cycle_no); reporter.print_report(cycle_no, elapsed)
        print(f"[SCAN] Completed in {elapsed:.1f}s | no HTF candidates")
        return

    h1_raw = fetch_stage(executor.market_exchange, aligned, "1h", Config.DATA_LIMIT, "SETUP", Config.SCAN_WORKERS)
    h1_df = {s: _to_df(h1_raw.get(s)) for s in aligned}
    setup_candidates = []
    stage_counts = {"aligned": len(aligned), "pd": 0, "zone": 0, "ready": 0}

    for s in aligned:
        snap = setup_snapshot(s, {"1d": d_df[s], "4h": h4_df[s], "1h": h1_df[s]})
        if snap.get("status") == "setup_ready":
            setup_candidates.append(s); stage_counts["ready"] += 1
        elif snap.get("status") == "waiting_zone":
            stage_counts["zone"] += 1
        elif snap.get("status") == "waiting_pd":
            stage_counts["pd"] += 1

        watchlist.update({**snap, "symbol": s})
        _print_strategy_state(s, snap)
        if reporter:
            reporter.add(_decision_from_snapshot(s, snap))

    print(f"[SETUP] 1H candidates: {len(setup_candidates)}/{len(aligned)}")
    print(f"[SETUP] Diagnostics: aligned={stage_counts['aligned']} | wrong-P/D={stage_counts['pd']} | no-zone={stage_counts['zone']} | setup-ready={stage_counts['ready']}")

    if not setup_candidates:
        elapsed=time.perf_counter()-started
        if reporter:
            reporter.save(cycle_no); reporter.print_report(cycle_no, elapsed)
        print(f"[SCAN] Completed in {elapsed:.1f}s | no 1H setups")
        return

    # Session is an execution filter, not a visibility filter. Report every
    # LONG/SHORT setup even when the current 15m candle is outside the
    # configured London/New York window; do not waste API calls until a
    # trading session is active.
    session_active = True
    session_sample = None
    for s in setup_candidates:
        if h1_df.get(s) is not None and len(h1_df[s]):
            session_sample = pd.Timestamp(h1_df[s].timestamp.iloc[-1])
            break
    if Config.SESSION_FILTER_ENABLED and session_sample is not None:
        session_active = session_allowed(session_sample)

    m15_df = {}
    if session_active:
        m15_raw = fetch_stage(executor.market_exchange, setup_candidates, "15m", Config.DATA_LIMIT, "TRIGGER", Config.SCAN_WORKERS)
        m15_df = {s: _to_df(m15_raw.get(s)) for s in setup_candidates}
    else:
        print("[TRIGGER] Session filter active: outside London/New York; keeping setups on watchlist without 15m execution scan.")
        for symbol in setup_candidates:
            snap = watchlist.get(symbol) or {}
            snap["status"]="waiting_session"
            snap["session"]=session_name(session_sample) if session_sample is not None else "OFF_HOURS"
            snap["reason"]="setup valid, but execution session is closed"
            watchlist.update({**snap,"symbol":symbol})
            if reporter:
                d=_decision_from_snapshot(symbol,snap)
                if d:
                    d.direction="LONG" if snap.get("side")=="bullish" else "SHORT"
                    d.setup=d.direction
                    d.action="WAIT"
                    d.state="WAITING_SESSION"
                    d.reason="setup valid, but execution session is closed"
                    reporter.add(d)
        elapsed=time.perf_counter()-started
        if reporter:
            reporter.save(cycle_no); reporter.print_report(cycle_no,elapsed)
        print(f"[SCAN] Completed cycle #{cycle_no} in {elapsed:.1f}s | setups retained for next session")
        return

    signals = []
    for symbol in setup_candidates:
        snap = watchlist.get(symbol) or {}
        if not risk.trading_allowed(equity):
            if reporter:
                reporter.add(CoinDecision(symbol=symbol, direction="LONG" if snap.get("side")=="bullish" else "SHORT",
                    bias=str(snap.get("bias","UNKNOWN")).upper(), state="RISK_BLOCKED", action="NO ENTRY",
                    reason="Risk manager blocked new trading", risk_reasons=["Trading not allowed by risk manager"]))
            print(f"[RISK] {symbol}: trading blocked")
            continue
        if symbol in risk.open_positions:
            if reporter:
                reporter.add(CoinDecision(symbol=symbol, direction="LONG" if snap.get("side")=="bullish" else "SHORT",
                    bias=str(snap.get("bias","UNKNOWN")).upper(), state="OPEN_POSITION", action="NO ENTRY",
                    reason="Position already open", risk_reasons=["Existing position"]))
            print(f"[SKIP] {symbol}: already open")
            continue
        if m15_df[symbol] is None:
            if reporter:
                reporter.add(CoinDecision(symbol=symbol, direction="LONG" if snap.get("side")=="bullish" else "SHORT",
                    bias=str(snap.get("bias","UNKNOWN")).upper(), state="TRIGGER_DATA_ERROR", action="NO ENTRY",
                    reason="15m data unavailable", execution_reasons=["Trigger timeframe data unavailable"]))
            continue

        candles={"1d":d_df[symbol],"4h":h4_df[symbol],"1h":h1_df[symbol],"15m":m15_df[symbol]}
        signal=analyze_symbol(symbol,candles)

        # Build an actual trigger snapshot for diagnostics, without weakening the strategy.
        side=snap.get("side")
        ss=market_structure(h1_df[symbol]); ts=market_structure(m15_df[symbol])
        sweep_level=ss.get("last_swing_low" if side=="bullish" else "last_swing_high")
        sweep=liquidity_sweep(m15_df[symbol],sweep_level,"below" if side=="bullish" else "above") if side else False
        disp=displacement(m15_df[symbol],side) if side else False
        mss=bool(side and ts.get("choch")==side)
        bos=bool(side and ts.get("bos")==side)
        near = None
        zone=snap.get("zone")
        if zone:
            price=float(m15_df[symbol].close.iloc[-1])
            if side=="bullish":
                near=price<=zone["top"]*(1+Config.SETUP_NEAR_PCT) and price>=zone["bottom"]*(1-Config.SETUP_NEAR_PCT)
            else:
                near=price>=zone["bottom"]*(1-Config.SETUP_NEAR_PCT) and price<=zone["top"]*(1+Config.SETUP_NEAR_PCT)

        trigger_snap=dict(snap)
        trigger_snap["checks"]=dict(snap.get("checks",{}))
        trigger_snap["checks"].update({
            "sweep":"YES" if sweep else "NO",
            "mss_bos":"MSS/CHoCH" if mss else ("BOS" if bos else "NONE"),
            "displacement":"YES" if disp else "NO",
            "liquidity":"SELL-SIDE" if side=="bullish" else "BUY-SIDE",
            "price_action": ("YES: "+",".join((price_action_confirmation(m15_df[symbol],side) or {}).get("patterns",[]))) if side else "NO",
        })

        if signal:
            signals.append(signal)
            trigger_snap["status"]="entry_ready"
            trigger_snap["reason"]="All strategy conditions passed"
            watchlist.update({**trigger_snap,"symbol":symbol,"signal":signal.to_dict()})
            print(f"[SIGNAL] {symbol} {signal.side.upper()} | score={signal.confluence} | RR={signal.risk_reward} | {signal.model}")
            if reporter:
                d=_decision_from_snapshot(symbol,trigger_snap,m15_df[symbol],signal)
                reporter.add(d)
        else:
            reasons=[]
            if not sweep: reasons.append("15M liquidity sweep not confirmed")
            if not (mss or bos): reasons.append("15M MSS/BOS not confirmed")
            if not disp: reasons.append("15M displacement not confirmed")
            pa_check=price_action_confirmation(m15_df[symbol],side) if side else None
            if Config.PA_REQUIRE_CONFIRMATION and not pa_check: reasons.append("15M price action confirmation not confirmed")
            if zone and near is False: reasons.append("price is not in the 1H POI")
            sr=snap.get("sr_zone")
            if sr:
                from smc_engine import sr_rejection, volume_confirmation
                rej=sr_rejection(m15_df[symbol],sr,side) if side else None
                vol=volume_confirmation(m15_df[symbol])
                if not rej: reasons.append("S/R rejection not confirmed")
                if Config.SR_REQUIRE_CONFIRMATION and not vol: reasons.append("S/R volume confirmation not met")
            # These are evaluated in analyze_symbol but aren't exposed directly; mirror them for explanation.
            if zone:
                price=float(m15_df[symbol].close.iloc[-1])
                a=atr(h1_df[symbol])
                if side=="bullish":
                    entry=float(zone["top"]); sl=min(float(zone["bottom"]),float(sweep_level or zone["bottom"]))-.20*a
                    targets=[p for p in swing_prices(ss.get("swing_highs",[])) if p>entry]
                else:
                    entry=float(zone["bottom"]); sl=max(float(zone["top"]),float(sweep_level or zone["top"]))+.20*a
                    targets=[p for p in swing_prices(ss.get("swing_lows",[])) if p<entry]
                tp=(min(targets) if side=="bullish" and targets else max(targets) if side=="bearish" and targets else None)
                if tp is None: reasons.append("no valid opposing liquidity target for TP")
                else:
                    rr=abs(tp-entry)/abs(entry-sl) if abs(entry-sl)>0 else 0
                    if rr<Config.MIN_RISK_REWARD: reasons.append(f"RR {rr:.2f} below minimum {Config.MIN_RISK_REWARD:.2f}")
                    if near is False: pass
                    score=20+(10 if snap["checks"].get("premium_discount_ok") else 0)+(15 if (mss or bos) else 0)+(15 if disp else 8)+(10 if snap["checks"].get("sr_level") else 0)+(5 if rej else 0)+(5 if vol else 0)+(5 if (bool(snap["checks"].get("order_block")) or bool(snap["checks"].get("fvg"))) else 0)+(15 if pa_check else 0)
                    if min(100,score)<Config.MIN_CONFLUENCE_SCORE: reasons.append(f"confluence {min(100,score):.0f} below minimum {Config.MIN_CONFLUENCE_SCORE}")
            if not reasons: reasons=["Strategy conditions did not produce a complete entry signal"]

            trigger_snap["status"]="trigger_rejected"
            trigger_snap["reason"]="; ".join(reasons)
            watchlist.update({**trigger_snap,"symbol":symbol})
            print(f"[SMC] {symbol} {('LONG' if side=='bullish' else 'SHORT')} | sweep={'YES' if sweep else 'NO'} | MSS/BOS={'MSS' if mss else 'BOS' if bos else 'NONE'} | displacement={'YES' if disp else 'NO'} | P/D={snap.get('checks',{}).get('h1_pd','?')} | ACTION=NO ENTRY")
            print(f"      REASON: {'; '.join(reasons)}")
            if reporter:
                reporter.add(_decision_from_snapshot(symbol,trigger_snap))

    print(f"[TRIGGER] Valid signals: {len(signals)}/{len(setup_candidates)}")

    for signal in signals:
        if not risk.trading_allowed(equity):
            print(f"[RISK] {signal.symbol}: blocked before execution")
            if reporter:
                reporter.add(CoinDecision(symbol=signal.symbol,direction="LONG" if signal.side=="long" else "SHORT",
                    bias=signal.metadata.get("htf_bias","UNKNOWN").upper(),setup=signal.side.upper(),
                    state="RISK_REJECTED",action="NO ENTRY",reason="Risk manager blocked execution",
                    risk_reasons=["Trading/risk limit blocked order"]))
            continue
        sizing=risk.size_position(equity,signal.entry,signal.stop_loss,signal.side)
        if sizing["notional_usdt"]<=0:
            print(f"[RISK] {signal.symbol}: position size is zero/too small")
            if reporter:
                reporter.add(CoinDecision(symbol=signal.symbol,direction=signal.side.upper(),bias=str(signal.metadata.get("htf_bias","UNKNOWN")).upper(),
                    setup=signal.side.upper(),state="RISK_REJECTED",action="NO ENTRY",
                    reason="Calculated position size is zero/too small",risk_reasons=["Position size below usable minimum"]))
            continue
        if not risk.can_add_exposure(signal.symbol,sizing["notional_usdt"],"crypto",equity):
            print(f"[RISK] {signal.symbol}: correlated exposure budget exceeded")
            if reporter:
                reporter.add(CoinDecision(symbol=signal.symbol,direction=signal.side.upper(),bias=str(signal.metadata.get("htf_bias","UNKNOWN")).upper(),
                    setup=signal.side.upper(),state="RISK_REJECTED",action="NO ENTRY",
                    reason="Correlated exposure budget exceeded",risk_reasons=["Maximum correlated exposure exceeded"]))
            continue

        executor.set_leverage(signal.symbol,sizing["suggested_leverage"])
        order=executor.place_trade(signal.symbol,signal.side,sizing["position_size_coin"],signal.entry,signal.stop_loss,signal.take_profit)
        if order:
            trade_id=uuid.uuid4().hex[:12]
            pending={"trade_id":trade_id,"order_id":order.get("id"),"amount":sizing["position_size_coin"],
                     "notional":sizing["notional_usdt"],"correlation_group":"crypto","entry":signal.entry,
                     "sl":signal.stop_loss,"tp":signal.take_profit,"side":signal.side,"model":signal.model,
                     "session":signal.session,"timeframe":"15m","rr":signal.risk_reward,
                     "confluence":signal.confluence,"risk_usdt":sizing["risk_usdt"],
                     "submitted_at_ms":int(datetime.now(timezone.utc).timestamp()*1000)}
            status=str(order.get("status","open")).lower()
            filled=float(order.get("filled") or 0)
            if status=="closed" or filled>=sizing["position_size_coin"]*0.999:
                protections=executor.place_protection(signal.symbol,signal.side,filled or sizing["position_size_coin"],signal.stop_loss,signal.take_profit)
                if len(protections)>=2:
                    risk.opened(signal.symbol,pending)
                    print(f"[EXECUTION] {signal.symbol}: order FILLED | protection={len(protections)}/2 | id={order.get('id')}")
                else:
                    print(f"[CRITICAL] {signal.symbol}: entry filled but protection incomplete; investigate immediately")
            else:
                risk.pending(signal.symbol,pending)
                print(f"[EXECUTION] {signal.symbol}: entry PENDING | status={status} | id={order.get('id')}")
            log("ENTRY",trade_id=trade_id,time=datetime.now(timezone.utc).isoformat(),symbol=signal.symbol,side=signal.side,model=signal.model,
                session=signal.session,timeframe="15m",entry=signal.entry,sl=signal.stop_loss,tp=signal.take_profit,rr=signal.risk_reward,
                confluence=signal.confluence,risk_usdt=sizing["risk_usdt"],notional_usdt=sizing["notional_usdt"],
                status=status,reason=signal.reason)
            if reporter:
                reporter.add(CoinDecision(symbol=signal.symbol,direction=signal.side.upper(),bias=signal.metadata.get("htf_bias","UNKNOWN").upper(),
                    setup=signal.side.upper(),state="EXECUTED",action="EXECUTED",reason=signal.reason,
                    entry=signal.entry,stop_loss=signal.stop_loss,take_profit=signal.take_profit,rr=signal.risk_reward,score=signal.confluence))
        else:
            print(f"[EXECUTION] {signal.symbol}: order NOT accepted")
            if reporter:
                reporter.add(CoinDecision(symbol=signal.symbol,direction=signal.side.upper(),bias=signal.metadata.get("htf_bias","UNKNOWN").upper(),
                    setup=signal.side.upper(),state="EXECUTION_REJECTED",action="NO ENTRY",
                    reason="Exchange did not accept entry order",execution_reasons=["Order placement failed or spread/precision rejected"]))

    elapsed=time.perf_counter()-started
    if reporter:
        reporter.save(cycle_no)
        reporter.print_report(cycle_no,elapsed)
    print(f"[SCAN] Completed cycle #{cycle_no} in {elapsed:.1f}s")

def main():
    banner()
    print("[BOOT] Python runtime OK")
    print(f"[BOOT] Timeout={Config.API_TIMEOUT_MS}ms | Retries={Config.API_RETRIES} | Workers={Config.SCAN_WORKERS}")
    try:
        Config.validate()
    except Exception as e:
        print(f"[FATAL] Configuration error: {e}")
        input("Press Enter to exit...")
        return

    if not Config.DEMO_MODE:
        if input("Type LIVE to enable real-money execution: ").strip() != "LIVE":
            return

    try:
        print("[BOOT] Initializing Bitget executor...")
        executor = BitgetExecutor()
        risk = RiskManager()
        watchlist = Watchlist()
        print("[BOOT] Initialization complete. Starting scan loop.")
        telegram.send_startup("DEMO" if Config.DEMO_MODE else "REAL")
    except KeyboardInterrupt:
        print("Stopped.")
        return
    except Exception as e:
        print("[FATAL] Startup failed:", repr(e))
        traceback.print_exc()
        input("\nPress Enter to exit...")
        return

    cycle = 0
    while True:
        cycle += 1
        try:
            run_once(executor, risk, cycle, watchlist)
        except KeyboardInterrupt:
            print("Stopped.")
            break
        except Exception as e:
            print("[LOOP ERROR]", repr(e))
            traceback.print_exc()

        wait = max(1, Config.SCAN_INTERVAL_SECONDS)
        print(f"[SLEEP] Next scan in {wait}s")
        try:
            for remaining in range(wait, 0, -1):
                if remaining == wait or remaining <= 10:
                    print(f"\r[SLEEP] {remaining:>4}s remaining", end="", flush=True)
                time.sleep(1)
            print()
        except KeyboardInterrupt:
            print("\nStopped.")
            break


if __name__ == "__main__":
    main()
