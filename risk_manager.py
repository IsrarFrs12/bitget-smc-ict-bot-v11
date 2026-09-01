import json, os
from datetime import datetime, timezone
from config import Config

class RiskManager:
    def __init__(self):
        self.open_positions = {}
        self.pending_orders = {}
        self.day = None; self.week = None
        self.daily_pnl = 0.0; self.weekly_pnl = 0.0
        self.equity_peak = 0.0; self.start_equity = 0.0
        self.daily_trades = 0; self.consecutive_losses = 0
        self._load()

    def _load(self):
        path = Config.STATE_FILE
        try:
            with open(path) as f: data=json.load(f)
            self.__dict__.update(data)
            self.open_positions = data.get("open_positions", {})
            self.pending_orders = data.get("pending_orders", {})
        except (FileNotFoundError, json.JSONDecodeError): pass
        self._roll()

    def _save(self):
        os.makedirs(os.path.dirname(Config.STATE_FILE) or ".", exist_ok=True)
        data={k:v for k,v in self.__dict__.items() if not k.startswith("_")}
        with open(Config.STATE_FILE,"w") as f: json.dump(data,f,default=str,indent=2)

    def _roll(self):
        now=datetime.now(timezone.utc); day=now.date().isoformat(); week=f"{now.isocalendar().year}-W{now.isocalendar().week}"
        if self.day != day: self.day=day; self.daily_pnl=0.0; self.daily_trades=0; self.consecutive_losses=0
        if self.week != week: self.week=week; self.weekly_pnl=0.0
        self._save()

    def trading_allowed(self, equity=None):
        self._roll()
        if len(self.open_positions) >= Config.MAX_CONCURRENT_TRADES: return False
        if self.daily_pnl <= -abs(Config.MAX_DAILY_LOSS_PERCENT): return False
        if self.weekly_pnl <= -abs(Config.MAX_WEEKLY_LOSS_PERCENT): return False
        if self.daily_trades >= Config.MAX_DAILY_TRADES: return False
        if self.consecutive_losses >= Config.CONSECUTIVE_LOSS_COOLDOWN: return False
        if equity and self.equity_peak and (equity-self.equity_peak)/self.equity_peak*100 <= -Config.MAX_EQUITY_DRAWDOWN_PERCENT: return False
        return True

    def update_equity(self, equity):
        self.start_equity = self.start_equity or equity
        self.equity_peak = max(self.equity_peak, equity)
        self._save()

    def register_result(self, pnl_usdt, equity_before):
        self._roll(); pct=(pnl_usdt/equity_before*100) if equity_before else 0
        self.daily_pnl += pct; self.weekly_pnl += pct
        self.consecutive_losses = self.consecutive_losses + 1 if pnl_usdt < 0 else 0
        self._save()

    def size_position(self, equity, entry, stop, side=None):
        risk=equity*Config.RISK_PER_TRADE_PERCENT/100
        dist=abs(entry-stop)
        if dist<=0: raise ValueError("Invalid stop distance")
        size=risk/dist; notional=size*entry
        max_margin=equity*Config.MAX_MARGIN_PERCENT/100
        leverage=max(1,min(Config.MAX_LEVERAGE,int(round(notional/max_margin)) if max_margin else Config.MAX_LEVERAGE))
        return {"risk_usdt":risk,"position_size_coin":size,"notional_usdt":notional,
                "suggested_leverage":leverage,"margin_required_usdt":notional/leverage}

    def can_add_exposure(self, symbol, notional, correlated_open, equity):
        if symbol in self.open_positions: return False
        same_family=sum(x.get("notional",0) for x in self.open_positions.values() if x.get("correlation_group")==correlated_open)
        # Cap aggregate correlated notional so BTC/ETH/alts do not accidentally
        # multiply the same directional crypto-beta risk.
        budget=equity*Config.MAX_CORRELATED_EXPOSURE_PERCENT/100.0
        return same_family + notional <= budget

    def pending(self, symbol, payload):
        self.pending_orders[symbol]=payload
        self._save()

    def remove_pending(self, symbol):
        self.pending_orders.pop(symbol,None)
        self._save()

    def opened(self, symbol, payload):
        self.pending_orders.pop(symbol,None)
        self.open_positions[symbol]=payload
        self.daily_trades += 1
        self._save()

    def closed(self, symbol):
        self.open_positions.pop(symbol,None)
        self._save()
