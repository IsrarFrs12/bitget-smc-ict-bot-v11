"""Simple chronological walk-forward evaluator. Never shuffles market data."""
import argparse, pandas as pd
from backtest import load, run

def main():
    p=argparse.ArgumentParser(); p.add_argument("csv"); p.add_argument("--train-days",type=int,default=180); p.add_argument("--test-days",type=int,default=30); a=p.parse_args()
    df=load(a.csv); start=df.timestamp.min(); end=df.timestamp.max(); cursor=start
    while cursor < end:
        train_end=cursor+pd.Timedelta(days=a.train_days); test_end=train_end+pd.Timedelta(days=a.test_days)
        train=df[(df.timestamp>=cursor)&(df.timestamp<train_end)]; test=df[(df.timestamp>=train_end)&(df.timestamp<test_end)]
        if len(test)>250: print(train_end.date(),test_end.date(),run(test))
        cursor=train_end
if __name__=="__main__": main()
