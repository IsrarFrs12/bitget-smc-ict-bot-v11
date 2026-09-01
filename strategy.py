from dataclasses import dataclass, asdict
from typing import Optional
import pandas as pd
from config import Config
from smc_engine import (market_structure, find_last_order_block, find_last_fvg,
                         liquidity_sweep, displacement, atr, session_allowed,
                         session_name, find_sr_levels, nearest_sr, swing_prices,
                         sr_rejection, volume_confirmation, price_action_confirmation)

@dataclass
class TradeSignal:
    symbol:str; side:str; entry:float; stop_loss:float; take_profit:float; risk_reward:float; confluence:int; model:str; session:str; reason:str; metadata:dict
    def to_dict(self): return asdict(self)

def _bias(d,h4):
    if d["bias"]==h4["bias"] and d["bias"]!="neutral": return d["bias"]
    if d["bias"] in ("bullish","bearish") and h4["bias"]=="neutral": return d["bias"]
    return None

def _overlap(a,b):
    if not a or not b:return None
    lo,hi=max(a["bottom"],b["bottom"]),min(a["top"],b["top"])
    return {"bottom":lo,"top":hi,"age":min(a.get("age",999),b.get("age",999))} if lo<hi else None

def _project_trade_plan(h1, side, zone, sr_zone=None):
    """Build a structural 1H projection without using 15m trigger data.

    The projected plan is informational/watchlist state only. A real order is
    still gated by the 15m trigger in analyze_symbol().
    """
    if h1 is None or len(h1) < Config.MIN_STRUCTURE_CANDLES or side not in ("bullish", "bearish"):
        return None
    a = atr(h1)
    if not pd.notna(a) or float(a) <= 0:
        return None

    ss = market_structure(h1)
    entry_zone = zone or sr_zone
    if not entry_zone:
        return None

    if zone:
        entry = float(zone["top"] if side == "bullish" else zone["bottom"])
        zone_bottom, zone_top = float(zone["bottom"]), float(zone["top"])
    else:
        entry = float(sr_zone["center"])
        zone_bottom, zone_top = float(sr_zone["bottom"]), float(sr_zone["top"])

    if side == "bullish":
        sl = min(zone_bottom, entry) - 0.20 * float(a)
        candidates = sorted(p for p in swing_prices(ss.get("swing_highs", [])) if p > entry)
    else:
        sl = max(zone_top, entry) + 0.20 * float(a)
        candidates = sorted((p for p in swing_prices(ss.get("swing_lows", [])) if p < entry), reverse=True)

    risk = abs(entry - sl)
    if risk <= 0:
        return None

    # Prefer the nearest confirmed structural target that still satisfies the
    # configured minimum RR. This avoids projecting an arbitrary target.
    target = None
    for p in candidates:
        rr = abs(p - entry) / risk
        if rr >= Config.MIN_RISK_REWARD:
            target = float(p)
            break
    if target is None:
        return None

    rr = abs(target - entry) / risk
    return {
        "timeframe": "1H",
        "entry": round(entry, 8),
        "stop_loss": round(sl, 8),
        "take_profit": round(target, 8),
        "rr": round(rr, 2),
        "source": "1H SMC zone + confirmed 1H swing target" if zone else "1H validated S/R + confirmed 1H swing target",
        "status": "PROJECTED",
    }


def setup_snapshot(symbol,candles):
    """Return an explainable 1H setup snapshot. 15m is not required here."""
    d,h4,h1=[candles.get(x) for x in ("1d","4h","1h")]
    out={"symbol":symbol,"status":"invalid","side":None,"checks":{},"score":0,"reason":""}
    if any(x is None or len(x)<Config.MIN_STRUCTURE_CANDLES for x in (d,h4,h1)):
        out["reason"]="insufficient confirmed candles"; return out
    ts=pd.Timestamp(h1.timestamp.iloc[-1])
    out["session"]=session_name(ts)
    # Session filtering is execution-only. We still analyze HTF/1H context
    # outside the killzone so the bot can report LONG/SHORT watch setups.
    ds,hs,ss=market_structure(d),market_structure(h4),market_structure(h1)
    bias=_bias(ds,hs); out["bias"]=bias
    out["checks"].update({"daily_4h_alignment":bool(bias),"h1_structure":ss["bias"],"h1_pd":ss.get("premium_discount")})
    if not bias: out["status"]="no_htf_bias"; out["reason"]="1D/4H have no aligned directional bias"; return out
    side=bias; out["side"]=side
    # Setup context: do not demand a fresh sweep on 1H. The 1H stage identifies
    # a meaningful location and directional structure; 15m confirms execution.
    pd_zone=ss.get("premium_discount")
    pd_ok=(side=="bullish" and pd_zone=="discount") or (side=="bearish" and pd_zone=="premium")
    out["checks"]["premium_discount_ok"]=pd_ok
    if ss["bias"] not in (side,"neutral"):
        out["status"]="h1_conflict"; out["reason"]="1H structure contradicts HTF bias"; return out
    ob=find_last_order_block(h1,side); fvg=find_last_fvg(h1,side)
    zone=_overlap(ob,fvg) or ob or fvg
    zone_ok=bool(zone and zone.get("age",999)<=Config.SETUP_MAX_AGE_BARS)
    sr=nearest_sr(h1,side)
    sr_ok=bool(sr and sr.get("score",0)>=Config.SR_MIN_SCORE and sr.get("touches",0)>=2 and sr.get("reactions",0)>=1 and sr.get("breaks",0)<=1)
    out["checks"].update({"order_block":bool(ob),"fvg":bool(fvg),"entry_zone":zone_ok,
                          "sr_level":sr_ok,"sr_score":sr.get("score",0) if sr else 0,"sr_touches":sr.get("touches",0) if sr else 0,"sr_reactions":sr.get("reactions",0) if sr else 0,"sr_breaks":sr.get("breaks",0) if sr else 0})
    out["zone"]=zone; out["sr_zone"]=sr
    # Create the 1H projected plan once the setup itself is valid. This is
    # deliberately independent from the 15m trigger so Entry/SL/TP can be
    # displayed before the execution session opens.
    # SMC route requires correct premium/discount location. Strong S/R is a
    # separate setup family and may qualify at a validated level even when P/D
    # is neutral/misaligned; the 15m rejection + structure gate remains hard.
    if not zone_ok and not sr_ok:
        out["status"]="waiting_zone"; out["reason"]="no fresh 1H OB/FVG and no strong nearby S/R zone"; return out
    if zone_ok and pd_ok and sr_ok:
        out["setup_model"]="smc_sr_confluence"; out["status"]="setup_ready"
        out["reason"]="HTF aligned + correct P/D + fresh SMC zone + strong S/R"
    elif zone_ok and pd_ok:
        out["setup_model"]="smc"; out["status"]="setup_ready"
        out["reason"]="HTF aligned + correct P/D + fresh 1H SMC zone"
    elif sr_ok:
        out["setup_model"]="sr_rejection"; out["status"]="setup_ready"
        out["reason"]="HTF aligned + validated horizontal S/R; P/D is not used as an S/R entry gate"
    else:
        out["status"]="waiting_pd"; out["reason"]=f"waiting for {('discount' if side=='bullish' else 'premium')} for SMC route"
    if out.get("status")=="setup_ready":
        out["projected_plan"]=_project_trade_plan(h1, side, zone if zone_ok else None, sr if sr_ok else None)
        if not out.get("projected_plan"):
            out["checks"]["projected_plan"]="unavailable"
    return out

def analyze_symbol(symbol,candles)->Optional[TradeSignal]:
    snap=setup_snapshot(symbol,candles)
    if snap.get("status")!="setup_ready": return None
    if Config.SESSION_FILTER_ENABLED:
        ts=pd.Timestamp(candles["15m"].timestamp.iloc[-1])
        if not session_allowed(ts): return None

    h1,m15=[candles[x] for x in ("1h","15m")]
    side=snap["side"]; ss=market_structure(h1); ts=market_structure(m15)
    price=float(m15.close.iloc[-1]); a=atr(h1)
    if not pd.notna(a) or a<=0:return None

    # SMC trigger
    sweep_level=ss.get("last_swing_low" if side=="bullish" else "last_swing_high")
    sweep=liquidity_sweep(m15,sweep_level,"below" if side=="bullish" else "above")
    disp=displacement(m15,side)
    mss=ts.get("choch")==side; bos=ts.get("bos")==side
    structure_confirm=mss or bos

    # Horizontal S/R trigger
    sr=snap.get("sr_zone")
    rejection=sr_rejection(m15,sr,side) if sr else None
    vol_ok=volume_confirmation(m15)
    pa=price_action_confirmation(m15,side)
    pa_ok=bool(pa and pa.get("score",0)>=Config.PA_MIN_SCORE)
    sr_confirm=bool(rejection and (vol_ok or not Config.SR_REQUIRE_CONFIRMATION))

    ob=fvg=zone=snap.get("zone")
    smc_zone=zone if zone else sr
    model=snap.get("setup_model","")
    # Require either the classic SMC trigger chain OR a strong S/R rejection
    # with market-structure confirmation. This prevents "touch = trade".
    if model=="sr_rejection":
        if not sr_confirm or not structure_confirm or (Config.PA_REQUIRE_CONFIRMATION and not pa_ok): return None
        entry=price
        if side=="bullish":
            sl=float(sr["bottom"])-0.20*a
            targets=[p for p in swing_prices(ss.get("swing_highs",[])) if p>entry]
            tp=min(targets) if targets else None
        else:
            sl=float(sr["top"])+0.20*a
            targets=[p for p in swing_prices(ss.get("swing_lows",[])) if p<entry]
            tp=max(targets) if targets else None
        trigger_name="S/R rejection + "+("MSS/CHoCH" if mss else "BOS")
    else:
        if not sweep or not structure_confirm or not disp: 
            # Allow SMC+S/R confluence to use a confirmed S/R rejection when
            # liquidity sweep is absent, but never without structure.
            if not (sr_confirm and structure_confirm and (pa_ok or not Config.PA_REQUIRE_CONFIRMATION)): return None
        if smc_zone:
            entry=float(smc_zone["top"] if side=="bullish" else smc_zone["bottom"])
        else: entry=price
        if side=="bullish":
            sl=min(float(smc_zone["bottom"]),float(sweep_level if sweep_level is not None else smc_zone["bottom"]))-0.20*a
            targets=[p for p in swing_prices(ss.get("swing_highs",[])) if p>entry]; tp=min(targets) if targets else None
        else:
            sl=max(float(smc_zone["top"]),float(sweep_level if sweep_level is not None else smc_zone["top"]))+0.20*a
            targets=[p for p in swing_prices(ss.get("swing_lows",[])) if p<entry]; tp=max(targets) if targets else None
        near=price<=smc_zone["top"]*(1+Config.SETUP_NEAR_PCT) and price>=smc_zone["bottom"]*(1-Config.SETUP_NEAR_PCT)
        if not near and not sr_confirm:return None
        trigger_name=("liquidity sweep + " if sweep else "S/R rejection + ")+("MSS/CHoCH" if mss else "BOS")
    if tp is None:return None
    risk=abs(entry-sl); reward=abs(tp-entry)
    if risk<=0:return None
    rr=reward/risk
    if rr<Config.MIN_RISK_REWARD:return None

    score=0
    score += 20 if snap["checks"].get("daily_4h_alignment") else 0
    score += 10 if snap["checks"].get("premium_discount_ok") else 0
    score += 15 if structure_confirm else 0
    score += 15 if disp else 8
    score += 10 if snap["checks"].get("sr_level") else 0
    score += 5 if rejection else 0
    score += 5 if vol_ok else 0
    score += 5 if (snap["checks"].get("order_block") or snap["checks"].get("fvg")) else 0
    score += 15 if pa_ok else 0
    score=min(100,int(score))
    if score<Config.MIN_CONFLUENCE_SCORE:return None

    if rejection and (snap["checks"].get("order_block") or snap["checks"].get("fvg")):
        model="smc_sr_confluence"
    elif rejection:
        model="sr_rejection"
    elif snap["checks"].get("fvg"):
        model="liquidity_sweep_mss_fvg"
    else:
        model="liquidity_sweep_mss_ob"

    reasons=(f"HTF {side} | {snap['reason']} | {trigger_name} | "
             f"{'displacement | ' if disp else ''}"
             f"{'volume confirmed | ' if vol_ok else ''}{'price action: '+','.join(pa.get('patterns',[]))+' | ' if pa_ok else ''}RR {rr:.2f}")
    return TradeSignal(symbol,side,round(entry,8),round(sl,8),round(tp,8),round(rr,2),
      score,model,session_name(m15.timestamp.iloc[-1]),reasons,
      {"htf_bias":side,"pd_zone":ss.get("premium_discount"),"sweep_level":sweep_level,
       "sweep":sweep,"displacement":disp,"mss":mss,"choch":bool(ts.get("choch")==side),"bos":bos,
       "ob":snap["checks"].get("order_block"),"fvg":snap["checks"].get("fvg"),
       "sr_zone":sr,"sr_score":sr.get("score",0) if sr else 0,
       "sr_rejection":rejection,"volume_confirmed":vol_ok,"price_action":pa,"price_action_confirmed":pa_ok,"zone":smc_zone,"price":price})
