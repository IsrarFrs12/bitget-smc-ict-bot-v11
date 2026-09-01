"""Empirical trade statistics.

These numbers are historical sample statistics from closed trades/backtests.
They are not a forecast or guaranteed probability of the next trade.
"""
import os
import pandas as pd

def _read(path):
    if not os.path.exists(path): return pd.DataFrame()
    try:
        df=pd.read_csv(path)
    except Exception:
        return pd.DataFrame()
    if df.empty or "event" not in df.columns:return pd.DataFrame()
    df=df[df.event.astype(str).str.upper()=="EXIT"].copy()
    if "pnl_usdt" not in df.columns:return pd.DataFrame()
    df["pnl_usdt"]=pd.to_numeric(df["pnl_usdt"],errors="coerce")
    return df.dropna(subset=["pnl_usdt"])

def summarize(df):
    if df is None or df.empty:
        return {"sample_size":0,"win_rate_pct":None,"profit_factor":None,"expectancy_usdt":None}
    wins=df.loc[df.pnl_usdt>0,"pnl_usdt"]
    losses=df.loc[df.pnl_usdt<0,"pnl_usdt"]
    pf=float(wins.sum()/abs(losses.sum())) if losses.sum()!=0 else (float("inf") if len(wins) else None)
    return {
        "sample_size":int(len(df)),
        "win_rate_pct":round(float((df.pnl_usdt>0).mean()*100),2),
        "profit_factor":round(pf,2) if pf is not None and pf != float("inf") else pf,
        "expectancy_usdt":round(float(df.pnl_usdt.mean()),4),
    }

def historical_stats(path="logs/trades.csv", model=None, side=None, session=None, min_sample=1):
    df=_read(path)
    if model and "model" in df: df=df[df.model.astype(str)==str(model)]
    if side and "side" in df: df=df[df.side.astype(str)==str(side)]
    if session and "session" in df: df=df[df.session.astype(str)==str(session)]
    out=summarize(df)
    out["sufficient_sample"]=out["sample_size"]>=int(min_sample)
    return out

def model_report(path="logs/trades.csv"):
    df=_read(path)
    if df.empty or "model" not in df.columns:return []
    rows=[]
    for model,g in df.groupby("model"):
        x=summarize(g); x["model"]=model; rows.append(x)
    return sorted(rows,key=lambda x:(x["sample_size"],x["win_rate_pct"] or -1),reverse=True)
