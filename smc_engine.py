"""Confirmed-candle SMC/ICT research engine.

This is a rules-based approximation, not a claim of institutional order-flow
visibility. All swing calculations use confirmed candles.
"""
import numpy as np
import pandas as pd
from config import Config


def atr_series(df, period=None):
    period = period or Config.ATR_PERIOD
    prev = df.close.shift(1)
    tr = pd.concat([(df.high-df.low), (df.high-prev).abs(), (df.low-prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period, min_periods=period).mean()


def atr(df, period=None):
    v = atr_series(df, period).iloc[-1]
    return float(v) if pd.notna(v) else float("nan")


def find_swings(df, left=None, right=None):
    left = Config.SWING_LEFT if left is None else left
    right = Config.SWING_RIGHT if right is None else right
    out = df.copy()
    out["swing_high"] = False; out["swing_low"] = False
    if len(out) < left + right + 1: return out
    highs, lows = out.high.to_numpy(), out.low.to_numpy()
    for i in range(left, len(out)-right):
        hw = highs[i-left:i+right+1]; lw = lows[i-left:i+right+1]
        out.iloc[i, out.columns.get_loc("swing_high")] = highs[i] == hw.max() and np.argmax(hw) == left
        out.iloc[i, out.columns.get_loc("swing_low")] = lows[i] == lw.min() and np.argmin(lw) == left
    return out


def _structure_sequence(df):
    s = find_swings(df)
    highs = [(i, float(r.high)) for i, r in s[s.swing_high].iterrows()]
    lows = [(i, float(r.low)) for i, r in s[s.swing_low].iterrows()]
    return s, highs, lows


def _empty_structure():
    return {"bias":"neutral","bos":None,"choch":None,"last_swing_high":None,"last_swing_low":None,
            "dealing_range":None,"premium_discount":None,"equal_high":None,"equal_low":None,
            "swing_highs":[],"swing_lows":[],"external_high":None,"external_low":None}


def market_structure(df):
    if df is None or len(df) < Config.MIN_STRUCTURE_CANDLES:
        return _empty_structure()
    _, highs, lows = _structure_sequence(df)
    if len(highs) < 2 or len(lows) < 2: return _empty_structure()
    hh = highs[-1][1] > highs[-2][1]; hl = lows[-1][1] > lows[-2][1]
    lh = highs[-1][1] < highs[-2][1]; ll = lows[-1][1] < lows[-2][1]
    bias = "bullish" if hh and hl else "bearish" if lh and ll else "neutral"
    close = float(df.close.iloc[-1]); hi, lo = highs[-1][1], lows[-1][1]
    bos = "bullish" if close > hi else "bearish" if close < lo else None
    choch = "bullish" if bias == "bearish" and close > hi else "bearish" if bias == "bullish" and close < lo else None
    mid = (hi+lo)/2
    av = atr(df)
    tol = max(av * Config.EQUAL_LEVEL_ATR, 1e-12) if np.isfinite(av) else 1e-12
    eqh = highs[-2][1] if abs(highs[-1][1]-highs[-2][1]) <= tol else None
    eql = lows[-2][1] if abs(lows[-1][1]-lows[-2][1]) <= tol else None
    return {"bias":bias,"bos":bos,"choch":choch,"last_swing_high":hi,"last_swing_low":lo,
            "dealing_range":{"high":hi,"low":lo,"mid":mid},
            "premium_discount":"premium" if close > mid else "discount",
            "equal_high":eqh,"equal_low":eql,"swing_highs":highs[-8:],"swing_lows":lows[-8:],
            "external_high":max(x[1] for x in highs[-Config.LIQUIDITY_LEVELS:]),
            "external_low":min(x[1] for x in lows[-Config.LIQUIDITY_LEVELS:])}


def displacement(df, direction, lookback=Config.DISPLACEMENT_LOOKBACK):
    if df is None or len(df) < Config.ATR_PERIOD + 3: return None
    a = atr_series(df); start=max(Config.ATR_PERIOD, len(df)-lookback); best=None
    for i in range(start, len(df)):
        av=a.iloc[i]
        if pd.isna(av) or av <= 0: continue
        body=abs(float(df.close.iloc[i]-df.open.iloc[i])); rng=float(df.high.iloc[i]-df.low.iloc[i])
        if body < Config.MIN_DISPLACEMENT_ATR*float(av) or rng <= 0: continue
        if direction == "bullish" and df.close.iloc[i] > df.open.iloc[i]:
            best={"index":i,"body":body,"atr":float(av),"body_atr":body/float(av),"range":rng}
        elif direction == "bearish" and df.close.iloc[i] < df.open.iloc[i]:
            best={"index":i,"body":body,"atr":float(av),"body_atr":body/float(av),"range":rng}
    return best


def find_order_blocks(df, direction, lookback=None):
    lookback=lookback or Config.OB_LOOKBACK; sub=df.tail(lookback).reset_index(drop=True)
    if len(sub)<Config.ATR_PERIOD+4: return []
    a=atr_series(sub); results=[]
    for i in range(len(sub)-1,2,-1):
        av=a.iloc[i]
        if pd.isna(av): continue
        body=float(sub.close.iloc[i]-sub.open.iloc[i])
        if abs(body)<Config.MIN_DISPLACEMENT_ATR*float(av): continue
        if direction == "bullish" and body <= 0: continue
        if direction == "bearish" and body >= 0: continue
        for j in range(i-1,max(-1,i-Config.OB_SEARCH_BARS),-1):
            ob_body=float(sub.close.iloc[j]-sub.open.iloc[j])
            opposite=(ob_body<0) if direction=="bullish" else (ob_body>0)
            if opposite:
                results.append({"top":float(sub.high.iloc[j]),"bottom":float(sub.low.iloc[j]),"impulse_index":i,
                                "ob_index":j,"age":len(sub)-1-j,"type":direction})
                return results
    return results


def find_last_order_block(df,direction,lookback=None):
    z=find_order_blocks(df,direction,lookback); return z[0] if z else None


def find_fvgs(df,direction,lookback=Config.FVG_LOOKBACK):
    sub=df.tail(lookback).reset_index(drop=True); a=atr_series(sub); out=[]
    for i in range(2,len(sub)):
        av=a.iloc[i]
        if pd.isna(av): continue
        c1,c2,c3=sub.iloc[i-2],sub.iloc[i-1],sub.iloc[i]
        if direction=="bullish" and c1.high<c3.low:
            gap=float(c3.low-c1.high)
            if gap>=Config.FVG_MIN_ATR*float(av): out.append({"top":float(c3.low),"bottom":float(c1.high),"index":i,"age":len(sub)-1-i,"size":gap})
        elif direction=="bearish" and c1.low>c3.high:
            gap=float(c1.low-c3.high)
            if gap>=Config.FVG_MIN_ATR*float(av): out.append({"top":float(c1.low),"bottom":float(c3.high),"index":i,"age":len(sub)-1-i,"size":gap})
    return out


def find_last_fvg(df,direction,lookback=None):
    z=find_fvgs(df,direction,lookback or Config.FVG_LOOKBACK); return z[-1] if z else None



def swing_prices(points):
    """Return validated swing prices from either (index, price) tuples or raw prices."""
    out = []
    for point in points or []:
        try:
            value = point[1] if isinstance(point, (tuple, list)) else point
            value = float(value)
            if np.isfinite(value):
                out.append(value)
        except (TypeError, ValueError, IndexError):
            continue
    return out

def liquidity_pools(df):
    _, highs, lows=_structure_sequence(df)
    return {"buy_side":[x[1] for x in highs[-Config.LIQUIDITY_LEVELS:]],"sell_side":[x[1] for x in lows[-Config.LIQUIDITY_LEVELS:]]}


def liquidity_sweep(df, level, direction, lookback=None):
    if level is None or df is None or len(df)<3: return None
    recent=df.tail(lookback or Config.SWEEP_LOOKBACK)
    for idx,row in recent.iloc[:-1].iterrows():
        if direction=="below" and float(row.low)<level and float(row.close)>level:
            return {"index":int(idx),"level":float(level),"type":"sell_side_sweep","bars_ago":len(recent)-1-list(recent.index).index(idx)}
        if direction=="above" and float(row.high)>level and float(row.close)<level:
            return {"index":int(idx),"level":float(level),"type":"buy_side_sweep","bars_ago":len(recent)-1-list(recent.index).index(idx)}
    return None



def _cluster_levels(values, tolerance):
    """Cluster nearby confirmed swing prices into horizontal S/R zones."""
    levels=[]
    for v in sorted(float(x) for x in values if np.isfinite(x)):
        if not levels or abs(v-levels[-1]["center"]) > tolerance:
            levels.append({"prices":[v], "center":v})
        else:
            levels[-1]["prices"].append(v)
            levels[-1]["center"]=float(np.mean(levels[-1]["prices"]))
    return levels


def find_sr_levels(df, direction=None, lookback=120):
    """Build quality-scored horizontal S/R zones from confirmed swings.

    The score measures observed price-action evidence only. It is intentionally
    conservative: repeated touches without clean reactions do not create a
    "strong" level, and heavily broken levels are penalized.
    """
    if df is None or len(df) < Config.MIN_STRUCTURE_CANDLES:
        return []
    sub=df.tail(lookback).reset_index(drop=True)
    sw=find_swings(sub)
    av=atr(sub)
    price=float(sub.close.iloc[-1])
    if not np.isfinite(av) or av <= 0 or price <= 0:
        return []
    tol=max(av*Config.SR_ZONE_ATR, price*Config.SR_ZONE_PCT)
    raw=list(sub.loc[sw.swing_high,"high"].astype(float))+list(sub.loc[sw.swing_low,"low"].astype(float))
    zones=[]
    for c in _cluster_levels(raw,tol):
        lo=min(c["prices"])-tol*0.30; hi=max(c["prices"])+tol*0.30
        touches=0; reactions=0; breaks=0; last_touch=-1
        for i,r in sub.iterrows():
            rh,rl,ro,rc=map(float,(r.high,r.low,r.open,r.close))
            if rh>=lo and rl<=hi:
                touches += 1; last_touch=i
                rng=max(rh-rl,1e-12)
                lower=(min(ro,rc)-rl)/rng
                upper=(rh-max(ro,rc))/rng
                body=abs(rc-ro)/rng
                # A reaction must reject the level and close away from it.
                if direction=="bullish":
                    reacted=lower>=Config.SR_WICK_RATIO and rc>ro and body>=Config.SR_MIN_BODY_RATIO and rc>hi
                elif direction=="bearish":
                    reacted=upper>=Config.SR_WICK_RATIO and rc<ro and body>=Config.SR_MIN_BODY_RATIO and rc<lo
                else:
                    reacted=((lower>=Config.SR_WICK_RATIO and rc>ro) or
                             (upper>=Config.SR_WICK_RATIO and rc<ro)) and body>=Config.SR_MIN_BODY_RATIO
                if reacted: reactions += 1
            # A confirmed close decisively through a zone is evidence of failure.
            if i > 0 and ((float(r.close)>hi and direction=="bullish") or
                          (float(r.close)<lo and direction=="bearish")):
                breaks += 1
        dist=abs(price-float(c["center"]))/price
        if dist>Config.SR_MAX_DISTANCE_PCT and not (lo<=price<=hi):
            continue
        recency_score=10.0*max(0.0,1.0-(len(sub)-1-max(last_touch,0))/max(lookback*0.65,1))
        touch_score=min(touches,4)*7.5
        reaction_score=min(reactions,3)*10.0
        cluster_score=min(max(len(c["prices"])-1,0),3)*5.0
        proximity_score=10.0*max(0.0,1.0-dist/max(Config.SR_MAX_DISTANCE_PCT,1e-9))
        break_penalty=min(breaks,3)*9.0
        score=max(0.0,min(100.0,15.0+touch_score+reaction_score+cluster_score+recency_score+proximity_score-break_penalty))
        zones.append({"bottom":round(lo,12),"top":round(hi,12),"center":round(c["center"],12),
                      "touches":touches,"reactions":reactions,"breaks":breaks,"score":int(round(score)),
                      "distance_pct":round(dist*100,4),"last_touch":last_touch,
                      "source":"confirmed_swing_cluster"})
    # Prefer quality first, then proximity; do not let a distant high-score zone
    # hide a nearby valid level.
    zones.sort(key=lambda z:(z["score"],-z["distance_pct"]),reverse=True)
    return zones[:Config.SR_MAX_LEVELS]

def nearest_sr(df, side, max_distance_pct=None):
    zones=find_sr_levels(df,side)
    if not zones:return None
    price=float(df.close.iloc[-1]); maxd=Config.SR_MAX_DISTANCE_PCT if max_distance_pct is None else max_distance_pct
    eligible=[z for z in zones if abs(price-z["center"])/price<=maxd or
              (z["bottom"]<=price<=z["top"])]
    if not eligible:return None
    if side=="bullish":
        supports=[z for z in eligible if z["center"]<=price+max(price*0.002,1e-12)]
        return max(supports,key=lambda z:z["center"]) if supports else None
    resistances=[z for z in eligible if z["center"]>=price-max(price*0.002,1e-12)]
    return min(resistances,key=lambda z:z["center"]) if resistances else None


def sr_rejection(df, zone, side, lookback=None):
    if df is None or not zone:return None
    sub=df.tail(lookback or Config.SR_REJECTION_LOOKBACK)
    for i in range(max(0,len(sub)-Config.SR_REJECTION_LOOKBACK),len(sub)):
        r=sub.iloc[i]; rng=float(r.high-r.low)
        if rng<=0: continue
        if not (float(r.low)<=zone["top"] and float(r.high)>=zone["bottom"]): continue
        lower=float(min(r.open,r.close)-r.low)/rng
        upper=float(r.high-max(r.open,r.close))/rng
        body=abs(float(r.close-r.open))/rng
        if side=="bullish" and float(r.close)>float(r.open) and lower>=Config.SR_WICK_RATIO and body>=Config.SR_MIN_BODY_RATIO:
            return {"index":int(i),"type":"bullish_rejection","wick_ratio":lower}
        if side=="bearish" and float(r.close)<float(r.open) and upper>=Config.SR_WICK_RATIO and body>=Config.SR_MIN_BODY_RATIO:
            return {"index":int(i),"type":"bearish_rejection","wick_ratio":upper}
    return None


def volume_confirmation(df, lookback=20):
    if df is None or len(df)<lookback+2:return False
    v=float(df.volume.iloc[-1]); med=float(df.volume.iloc[-lookback-1:-1].median())
    return med>0 and v>=med*Config.SR_VOLUME_MULTIPLIER


def price_action_confirmation(df, direction, lookback=None):
    """Detect objective 15m price-action reactions in the trade direction.

    This is confirmation, not a standalone strategy: engulfing/rejection/strong
    close patterns are only considered after the higher-timeframe setup exists.
    Returns a dict with a score and detected pattern names, or None.
    """
    if df is None or len(df) < 3 or direction not in ("bullish", "bearish"):
        return None
    sub = df.tail(lookback or Config.PA_LOOKBACK).reset_index(drop=True)
    if len(sub) < 2:
        return None
    patterns=[]; best=None
    for i in range(1, len(sub)):
        r=sub.iloc[i]; prev=sub.iloc[i-1]
        o,c,h,l=map(float,(r.open,r.close,r.high,r.low))
        po,pc=map(float,(prev.open,prev.close))
        rng=h-l
        if rng<=0: continue
        body=abs(c-o); upper=h-max(o,c); lower=min(o,c)-l
        close_pos=(c-l)/rng
        # Engulfing: current body consumes the previous candle body.
        bullish_engulf = c>o and pc<po and o<=pc and c>=po
        bearish_engulf = c<o and pc>po and o>=pc and c<=po
        # Rejection/pin: directional wick plus meaningful body/close.
        bullish_pin = c>o and lower/rng>=0.45 and body/rng>=0.20 and close_pos>=0.60
        bearish_pin = c<o and upper/rng>=0.45 and body/rng>=0.20 and close_pos<=0.40
        # Strong close: large directional body with close near candle extreme.
        bullish_close = c>o and body/rng>=0.60 and close_pos>=0.75
        bearish_close = c<o and body/rng>=0.60 and close_pos<=0.25
        found=[]
        if direction=="bullish":
            if bullish_engulf: found.append("bullish_engulfing")
            if bullish_pin: found.append("bullish_rejection")
            if bullish_close: found.append("bullish_strong_close")
        else:
            if bearish_engulf: found.append("bearish_engulfing")
            if bearish_pin: found.append("bearish_rejection")
            if bearish_close: found.append("bearish_strong_close")
        if found:
            patterns.extend(found)
            score=min(100, max(1, len(set(found))*2 + (2 if (bullish_engulf or bearish_engulf) else 0)))
            best={"index":i,"patterns":found,"score":score,"body_ratio":round(body/rng,3),"close_position":round(close_pos,3)}
    if not best:
        return None
    best["patterns"]=list(dict.fromkeys(patterns))
    best["score"]=max(best["score"], min(100, 2*len(best["patterns"])))
    return best

def session_name(timestamp):
    ts=pd.Timestamp(timestamp); h=ts.hour
    if Config.LONDON_START_UTC<=h<Config.LONDON_END_UTC: return "london"
    if Config.NEW_YORK_START_UTC<=h<Config.NEW_YORK_END_UTC: return "new_york"
    if h<Config.LONDON_START_UTC: return "asia"
    return "off_hours"


def session_allowed(timestamp):
    if not Config.SESSION_FILTER_ENABLED: return True
    n=session_name(timestamp)
    return n in ("london","new_york") or (n=="asia" and Config.ALLOW_ASIA)
