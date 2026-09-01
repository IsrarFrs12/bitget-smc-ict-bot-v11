import csv, os
from config import Config

FIELDS=["trade_id","time","event","symbol","side","model","session","timeframe",
        "entry","exit_price","sl","tp","rr","confluence","risk_usdt","notional_usdt",
        "status","pnl_usdt","exit_reason","duration_min","reason"]

def _migrate_if_needed():
    path=Config.JOURNAL_FILE
    if not os.path.exists(path): return
    try:
        with open(path,newline="",encoding="utf-8") as f:
            reader=csv.DictReader(f); old_fields=reader.fieldnames or []
            rows=list(reader)
        if old_fields==FIELDS:return
        with open(path,"w",newline="",encoding="utf-8") as f:
            w=csv.DictWriter(f,fieldnames=FIELDS); w.writeheader()
            for row in rows:w.writerow({k:row.get(k,"") for k in FIELDS})
    except Exception:
        pass

def log(event, **kwargs):
    os.makedirs(os.path.dirname(Config.JOURNAL_FILE) or ".", exist_ok=True)
    _migrate_if_needed()
    exists=os.path.exists(Config.JOURNAL_FILE)
    row={k:kwargs.get(k,"") for k in FIELDS}; row["event"]=event
    with open(Config.JOURNAL_FILE,"a",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS)
        if not exists: w.writeheader()
        w.writerow(row)
