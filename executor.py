import time
import ccxt
import pandas as pd
from config import Config

class BitgetExecutor:
    def __init__(self):
        Config.validate()
        self._last_equity = 0.0
        self.exchange=ccxt.bitget({"apiKey":Config.API_KEY,"secret":Config.API_SECRET,"password":Config.API_PASSWORD,
                                   "options":{"defaultType":"swap"},"enableRateLimit":True,
                                   "timeout":Config.API_TIMEOUT_MS})
        # Keep public market-data access independent from private demo/live API routing.
        # Public ticker endpoint is often slower than account/ohlcv requests.
        # Give the market-data client its own longer timeout so a transient ticker
        # delay does not poison the whole scan loop.
        self.market_exchange=ccxt.bitget({"options":{"defaultType":"swap"},"enableRateLimit":True,
                                          "timeout":max(Config.API_TIMEOUT_MS, Config.MARKET_TICKER_TIMEOUT_MS)})
        print("[BOOT] Bitget clients created")
        if Config.DEMO_MODE:
            print("[BOOT] Enabling Bitget demo trading endpoint...")
            self.exchange.enable_demo_trading(True)
        print("[BOOT] Loading public markets...")
        self.market_exchange.load_markets()
        print(f"[BOOT] Public markets loaded: {len(self.market_exchange.markets)}")
        print("[BOOT] Loading private/demo markets...")
        self.exchange.load_markets()
        print(f"[BOOT] Private/demo markets loaded: {len(self.exchange.markets)}")
        print("[MODE]", "DEMO" if Config.DEMO_MODE else "REAL")

    def _ohlcv_df(self, symbol, timeframe, limit=None):
        limit = limit or Config.DATA_LIMIT
        raw = self.market_exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        if not raw or len(raw) < 20:
            raise ValueError(f"{symbol} {timeframe}: insufficient candles")
        df = pd.DataFrame(raw, columns=["timestamp","open","high","low","close","volume"])
        df["timestamp"] = pd.to_datetime(df.timestamp, unit="ms", utc=True)
        return df.iloc[:-1].reset_index(drop=True)  # confirmed candles only

    def fetch_multi_timeframe(self, symbol, timeframes=("1d","4h","1h","15m")):
        out = {}
        for tf in timeframes:
            try:
                out[tf] = self._ohlcv_df(symbol, tf)
            except Exception as e:
                print(f"[WARN] {symbol} {tf}: {e}")
                out[tf] = None
        return out

    def get_equity_usdt(self):
        last_error=None
        for attempt in range(1, Config.API_RETRIES+1):
            try:
                b=self.exchange.fetch_balance(params={"type":"swap","productType":"USDT-FUTURES"})
                u=b.get("USDT",{}) or {}
                value=float(u.get("total") or u.get("free") or 0)
                if value>0:
                    self._last_equity=value
                    return value
            except Exception as e:
                last_error=e
            try:
                r=self.exchange.privateMixGetV2MixAccountAccounts({"productType":"USDT-FUTURES"})
                for a in r.get("data",[]):
                    if a.get("marginCoin")=="USDT":
                        value=float(a.get("accountEquity") or a.get("available") or 0)
                        if value>0:
                            self._last_equity=value
                            return value
            except Exception as e:
                last_error=e
            if attempt<Config.API_RETRIES:
                time.sleep(min(2**(attempt-1),2))
        print(f"[ERROR] equity unavailable after {Config.API_RETRIES} attempts: {last_error}")
        return 0.0

    def fetch_open_positions(self):
        try:
            return [p for p in self.exchange.fetch_positions() if float(p.get("contracts") or 0)>0]
        except Exception as e:
            print("[WARN] positions sync:",e)
            return None

    def set_leverage(self,symbol,leverage):
        try: self.exchange.set_leverage(leverage,symbol,params={"marginMode":"isolated"})
        except Exception as e: print(f"[WARN] leverage {symbol}: {e}")

    def spread_ok(self,symbol):
        try:
            t=self.market_exchange.fetch_ticker(symbol); bid,ask=t.get("bid"),t.get("ask")
            if not bid or not ask: return False
            return ((ask-bid)/((ask+bid)/2))*10000 <= Config.MAX_SPREAD_BPS
        except Exception: return False

    def place_trade(self,symbol,side,amount,entry,stop_loss,take_profit):
        if not Config.DEMO_MODE and not Config.LIVE_TRADING_ENABLED: raise RuntimeError("Live trading safety lock")
        if not self.spread_ok(symbol): print(f"[SKIP] {symbol}: spread too wide/unavailable"); return None
        amount=float(self.exchange.amount_to_precision(symbol,amount)); entry=float(self.exchange.price_to_precision(symbol,entry))
        sl=float(self.exchange.price_to_precision(symbol,stop_loss)); tp=float(self.exchange.price_to_precision(symbol,take_profit))
        if amount<=0:return None
        order_side="buy" if side=="long" else "sell"
        try:
            order=self.exchange.create_order(symbol,"limit",order_side,amount,entry,params={"reduceOnly":False})
            print(f"[ENTRY] {symbol}: {order.get('id')} status={order.get('status')}")
            return order
        except Exception as e: print(f"[ERROR] entry {symbol}: {e}"); return None


    def fetch_order_status(self, order_id, symbol):
        try:
            return self.exchange.fetch_order(order_id, symbol)
        except Exception as e:
            print(f"[WARN] order status {symbol}/{order_id}: {e}")
            return None

    def cancel_order(self, order_id, symbol):
        try:
            return self.exchange.cancel_order(order_id, symbol)
        except Exception as e:
            print(f"[WARN] cancel order {symbol}/{order_id}: {e}")
            return None

    def fetch_my_trades_since(self, symbol, since_ms=None, limit=100):
        try:
            return self.exchange.fetch_my_trades(symbol, since=since_ms, limit=limit)
        except Exception as e:
            print(f"[WARN] my trades {symbol}: {e}")
            return []

    def place_protection(self,symbol,side,amount,sl,tp):
        # Protection is submitted only after the entry has actually filled.
        # Exact trigger parameter names vary by Bitget/ccxt version, so failures are surfaced loudly.
        exit_side="sell" if side=="long" else "buy"
        results=[]
        for trigger,kind in ((sl,"stopLoss"),(tp,"takeProfit")):
            try:
                params={"reduceOnly":True, kind:{"triggerPrice":float(self.exchange.price_to_precision(symbol,trigger))}}
                results.append(self.exchange.create_order(symbol,"market",exit_side,amount,None,params=params))
            except Exception as e: print(f"[CRITICAL] protection {symbol} {kind} failed: {e}")
        return results
