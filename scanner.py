import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from config import Config

def _safe_float(v,d=0.0):
    try:return float(v or 0)
    except:return d

def _load_ticker_cache():
    try:
        with open(Config.TICKER_CACHE_FILE, "r", encoding="utf-8") as f:
            data=json.load(f)
        ts=float(data.get("saved_at", 0)); symbols=data.get("symbols", [])
        if not isinstance(symbols, list) or not symbols:
            return []
        age=time.time()-ts
        if age <= Config.TICKER_CACHE_TTL_SECONDS:
            return [str(s) for s in symbols]
        print(f"[SCAN] Liquid-universe cache expired ({age:.0f}s old)")
    except Exception:
        pass
    return []

def _save_ticker_cache(symbols):
    if not symbols:
        return
    try:
        parent=os.path.dirname(Config.TICKER_CACHE_FILE)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp=Config.TICKER_CACHE_FILE+".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"saved_at":time.time(), "symbols":list(symbols)}, f, indent=2)
        os.replace(tmp, Config.TICKER_CACHE_FILE)
    except Exception as e:
        print(f"[WARN] Could not save liquid-universe cache: {e}")

def get_tradable_symbols(exchange):
    markets=exchange.load_markets()
    symbols=[m["symbol"] for m in markets.values() if m.get("swap") and m.get("quote")==Config.QUOTE_CURRENCY and m.get("active",True)]

    last_error=None
    tickers=None
    for attempt in range(1, Config.TICKER_FETCH_RETRIES+1):
        started=time.perf_counter()
        try:
            # Bitget's bulk ticker endpoint can transiently time out even though
            # the rest of the API is healthy. Retry this stage independently.
            tickers=exchange.fetch_tickers(symbols)
            print(f"[SCAN] Bulk ticker fetch OK in {time.perf_counter()-started:.1f}s")
            break
        except Exception as e:
            last_error=e
            print(f"[WARN] Bulk ticker fetch attempt {attempt}/{Config.TICKER_FETCH_RETRIES} failed: {e!r}")
            if attempt < Config.TICKER_FETCH_RETRIES:
                time.sleep(min(2 ** (attempt-1), 4))

    if tickers is not None:
        rows=[]
        for s in symbols:
            t=tickers.get(s,{}) or {}; q=_safe_float(t.get("quoteVolume")); bid=_safe_float(t.get("bid")); ask=_safe_float(t.get("ask")); mid=(bid+ask)/2 if bid and ask else 0
            spread=((ask-bid)/mid)*10000 if mid else 99999
            if q>=Config.MIN_24H_VOLUME_USDT and spread<=Config.MAX_SPREAD_BPS:
                rows.append((s,q,spread))
        rows.sort(key=lambda x:x[1],reverse=True)
        selected=[s for s,_,_ in rows[:Config.TOP_N_COINS_BY_VOLUME]]
        if selected:
            _save_ticker_cache(selected)
        print(f"[SCAN] Universe: {len(symbols)} perpetuals | liquid candidates: {len(selected)}")
        return selected

    cached=_load_ticker_cache()
    if cached:
        print(f"[SCAN] Bulk ticker unavailable; using cached liquid universe ({len(cached)} symbols)")
        return cached[:Config.TOP_N_COINS_BY_VOLUME]

    print(f"[ERROR] Unable to obtain liquid universe after {Config.TICKER_FETCH_RETRIES} attempts: {last_error!r}")
    print("[SCAN] No safe ticker cache available; skipping this scan without crashing the loop.")
    return []

def _fetch_one(exchange,symbol,timeframe,limit,retries=2):
    last=None
    for attempt in range(1,retries+1):
        started=time.perf_counter()
        try:
            raw=exchange.fetch_ohlcv(symbol,timeframe=timeframe,limit=limit)
            if not raw or len(raw)<20: raise ValueError(f"insufficient candles ({len(raw) if raw else 0})")
            return raw,time.perf_counter()-started,None
        except Exception as e:
            last=e
            if attempt<retries: time.sleep(min(2**(attempt-1),2))
    return None,time.perf_counter()-started,last

def fetch_stage(exchange,symbols,timeframe,limit,label,max_workers=5):
    results={}; total=len(symbols)
    if not total:return results
    print(f"[{label}] Fetching {timeframe}: {total} symbols | workers={max_workers}")
    done=0
    with ThreadPoolExecutor(max_workers=min(max_workers,total)) as pool:
        fs={pool.submit(_fetch_one,exchange,s,timeframe,limit,Config.API_RETRIES):s for s in symbols}
        for f in as_completed(fs):
            s=fs[f]; raw,elapsed,error=f.result(); done+=1
            if raw is not None: results[s]=raw
            else: print(f"[WARN] {label} {s}: {error}")
            if done==total or done%max(1,min(5,total))==0: print(f"[{label}] {done}/{total} complete")
    return results
