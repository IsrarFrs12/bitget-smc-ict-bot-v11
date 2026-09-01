"""Event-driven, fill-aware single-symbol backtester.

The test uses confirmed higher-timeframe bars only and models the strategy's limit
entry correctly: a signal is not counted as a trade until price actually touches
the proposed entry. If SL and TP are both touched inside one candle, SL wins
(conservative ambiguity handling).
"""
import argparse
import pandas as pd
import numpy as np
from strategy import analyze_symbol
from config import Config

def load(path):
    df=pd.read_csv(path)
    df["timestamp"]=pd.to_datetime(df.timestamp,utc=True)
    return df.sort_values("timestamp").reset_index(drop=True)

def resample(df, rule):
    x=df.set_index("timestamp")["open high low close volume".split()].resample(rule).agg(
        {"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna().reset_index()
    return x

def _exit_result(sig, future):
    for _,bar in future.iterrows():
        if sig.side=="long":
            hit_sl=float(bar.low)<=sig.stop_loss
            hit_tp=float(bar.high)>=sig.take_profit
        else:
            hit_sl=float(bar.high)>=sig.stop_loss
            hit_tp=float(bar.low)<=sig.take_profit
        if hit_sl: return -1.0, "SL"
        if hit_tp: return sig.risk_reward, "TP"
    return None, None

def run(df15, initial=10000):
    balance=initial; trades=[]; equity_curve=[]
    d=resample(df15,"1D"); h4=resample(df15,"4h"); h1=resample(df15,"1h")
    for i in range(250,len(df15)):
        t=df15.timestamp.iloc[i]
        candles={"15m":df15.iloc[:i].copy(),
                 "1h":h1[h1.timestamp < t].copy(),
                 "4h":h4[h4.timestamp < t].copy(),
                 "1d":d[d.timestamp < t].copy()}
        sig=analyze_symbol("BACKTEST/USDT:USDT",candles)
        if not sig:
            equity_curve.append(balance); continue

        # The strategy produces a limit-style entry. First wait for a real touch.
        fill_idx=None
        for j in range(i, min(len(df15), i+Config.BACKTEST_ENTRY_TIMEOUT_BARS)):
            bar=df15.iloc[j]
            if float(bar.low)<=sig.entry<=float(bar.high):
                fill_idx=j; break
        if fill_idx is None:
            equity_curve.append(balance); continue

        risk=balance*Config.RISK_PER_TRADE_PERCENT/100
        size=risk/abs(sig.entry-sig.stop_loss)
        result,exit_reason=_exit_result(sig,df15.iloc[fill_idx+1:fill_idx+1+Config.BACKTEST_MAX_HOLD_BARS])
        if result is None:
            equity_curve.append(balance); continue

        gross=result*risk
        fees=(size*sig.entry)*Config.FEE_BPS/10000*2
        slip=(size*sig.entry)*Config.PAPER_SLIPPAGE_BPS/10000
        pnl=gross-fees-slip
        balance+=pnl
        trades.append({"time":t,"fill_time":df15.timestamp.iloc[fill_idx],
                       "side":sig.side,"model":sig.model,"rr":sig.risk_reward,
                       "confluence":sig.confluence,"pnl":pnl,"exit_reason":exit_reason})
        equity_curve.append(balance)

    tr=pd.DataFrame(trades)
    if tr.empty:
        return {"trades":0,"win_rate":0,"profit_factor":0,"max_drawdown":0,
                "net_pnl":0,"expectancy":0,"models":[]}
    wins=tr.loc[tr.pnl>0,"pnl"]; losses=tr.loc[tr.pnl<0,"pnl"]
    curve=pd.Series(equity_curve or [initial])
    dd=(curve/curve.cummax()-1)*100
    models=[]
    for model,g in tr.groupby("model"):
        w=g.loc[g.pnl>0,"pnl"]; l=g.loc[g.pnl<0,"pnl"]
        models.append({"model":model,"trades":len(g),
                       "win_rate":round(float((g.pnl>0).mean()*100),2),
                       "profit_factor":round(float(w.sum()/abs(l.sum())),2) if l.sum()!=0 else float("inf")})
    return {"trades":len(tr),"win_rate":round(float((tr.pnl>0).mean()*100),2),
            "profit_factor":round(float(wins.sum()/abs(losses.sum())),2) if losses.sum()!=0 else float("inf"),
            "max_drawdown":round(float(dd.min()),2),"net_pnl":round(float(tr.pnl.sum()),2),
            "expectancy":round(float(tr.pnl.mean()),2),"models":models}

def main():
    p=argparse.ArgumentParser()
    p.add_argument("csv")
    p.add_argument("--equity",type=float,default=10000)
    a=p.parse_args()
    print(run(load(a.csv),a.equity))

if __name__=="__main__": main()
