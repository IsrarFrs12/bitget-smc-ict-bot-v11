from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import json, os

@dataclass
class CoinDecision:
    symbol: str
    direction: str = "NEUTRAL"       # LONG / SHORT / NEUTRAL
    bias: str = "UNKNOWN"
    setup: str = "NONE"
    structure: str = "NONE"
    liquidity: str = "NONE"
    sweep: str = "WAITING"
    mss_bos: str = "WAITING"
    displacement: str = "WAITING"
    fvg: str = "NONE"
    order_block: str = "NONE"
    sr_level: str = "NONE"
    sr_score: Optional[float] = None
    rejection: str = "NONE"
    volume_confirmation: str = "NO"
    price_action: str = "WAITING"
    premium_discount: str = "UNKNOWN"
    session: str = "UNKNOWN"
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    rr: Optional[float] = None
    projected_timeframe: str = "1H"
    projected_entry: Optional[float] = None
    projected_stop_loss: Optional[float] = None
    projected_take_profit: Optional[float] = None
    projected_rr: Optional[float] = None
    historical_win_rate_pct: Optional[float] = None
    historical_sample_size: int = 0
    historical_profit_factor: Optional[float] = None
    historical_expectancy_usdt: Optional[float] = None
    score: Optional[float] = None
    state: str = "SCANNED"
    action: str = "NO ENTRY"
    reason: str = ""
    strategy_reasons: List[str] = field(default_factory=list)
    risk_reasons: List[str] = field(default_factory=list)
    execution_reasons: List[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

class ExplainableReporter:
    """Human-readable per-coin decisions + aggregate scan diagnostics."""
    def __init__(self, state_dir="state", log_file="logs/decisions.jsonl"):
        self.state_dir = state_dir
        self.log_file = log_file
        os.makedirs(state_dir, exist_ok=True)
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        self.decisions: List[CoinDecision] = []
        self.counts = {
            "scanned": 0, "long": 0, "short": 0, "neutral": 0,
            "watching": 0, "setup_ready": 0, "entry_ready": 0,
            "executed": 0, "strategy_rejected": 0,
            "risk_rejected": 0, "execution_rejected": 0, "errors": 0
        }

    def add(self, d: CoinDecision):
        # One final row per symbol per scan. Trigger/execution stages update
        # the same symbol instead of duplicating it in the report.
        existing = next((x for x in self.decisions if x.symbol == d.symbol), None)
        if existing is not None:
            self.decisions.remove(existing)
        self.decisions.append(d)
        self._recount()

    def _recount(self):
        self.counts = {
            "scanned": len(self.decisions), "long": 0, "short": 0, "neutral": 0,
            "watching": 0, "setup_ready": 0, "entry_ready": 0,
            "executed": 0, "strategy_rejected": 0,
            "risk_rejected": 0, "execution_rejected": 0, "errors": 0
        }
        for d in self.decisions:
            key=d.direction.lower()
            if key in ("long","short","neutral"): self.counts[key]+=1
            st=d.state.lower()
            if "watch" in st or "wait" in st: self.counts["watching"]+=1
            if "setup" in st: self.counts["setup_ready"]+=1
            if "entry" in st or d.action=="ENTRY READY": self.counts["entry_ready"]+=1
            if d.action=="EXECUTED": self.counts["executed"]+=1
            if d.strategy_reasons: self.counts["strategy_rejected"]+=1
            if d.risk_reasons: self.counts["risk_rejected"]+=1
            if d.execution_reasons: self.counts["execution_rejected"]+=1

    def save(self, cycle: int):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "cycle": cycle,
            "summary": self.counts,
            "decisions": [d.to_dict() for d in self.decisions]
        }
        path=os.path.join(self.state_dir, "last_scan_report.json")
        with open(path,"w",encoding="utf-8") as f:
            json.dump(payload,f,indent=2,default=str)
        with open(self.log_file,"a",encoding="utf-8") as f:
            f.write(json.dumps(payload,default=str)+"\n")

    @staticmethod
    def _v(x):
        if x is None: return "-"
        if isinstance(x,float): return f"{x:.6g}"
        return str(x)

    def print_report(self, cycle: int, elapsed: float):
        print("\n" + "="*72)
        print(f"DECISION REPORT | SCAN #{cycle} | {elapsed:.1f}s")
        print("="*72)
        for i,d in enumerate(self.decisions,1):
            print(f"\n[{i:02d}] {d.symbol}")
            print(f"  Direction: {d.direction:<8} | Bias: {d.bias} | Setup: {d.setup}")
            print(f"  Structure: {d.structure} | Liquidity: {d.liquidity} | Sweep: {d.sweep}")
            print(f"  MSS/BOS: {d.mss_bos} | Displacement: {d.displacement}")
            print(f"  FVG: {d.fvg} | OB: {d.order_block} | P/D: {d.premium_discount}")
            print(f"  S/R: {d.sr_level} ({self._v(d.sr_score)}) | Rejection: {d.rejection} | Volume: {d.volume_confirmation} | Price Action: {d.price_action}")
            print(f"  Session: {d.session} | State: {d.state}")
            print(f"  1H PLAN: Entry={self._v(d.projected_entry)} | SL={self._v(d.projected_stop_loss)} | TP={self._v(d.projected_take_profit)} | RR={self._v(d.projected_rr)}")
            print(f"  15M CONFIRMED: Entry={self._v(d.entry)} | SL={self._v(d.stop_loss)} | TP={self._v(d.take_profit)} | RR={self._v(d.rr)}")
            wr = "-" if d.historical_win_rate_pct is None else f"{d.historical_win_rate_pct:.2f}%"
            print(f"  Historical win rate: {wr} | N={d.historical_sample_size} | PF={self._v(d.historical_profit_factor)} | Exp={self._v(d.historical_expectancy_usdt)} USDT")
            print(f"  Score: {self._v(d.score)} | ACTION: {d.action}")
            if d.reason:
                print(f"  REASON: {d.reason}")
            for label, vals in (
                ("Strategy", d.strategy_reasons),
                ("Risk", d.risk_reasons),
                ("Execution", d.execution_reasons),
            ):
                if vals:
                    print(f"  {label} rejection: " + "; ".join(vals))

        print("\n" + "-"*72)
        print("SCAN SUMMARY")
        print("-"*72)
        c=self.counts
        print(f"Scanned: {c['scanned']} | LONG: {c['long']} | SHORT: {c['short']} | NEUTRAL: {c['neutral']}")
        print(f"Watching: {c['watching']} | Setup-ready: {c['setup_ready']} | Entry-ready: {c['entry_ready']} | Executed: {c['executed']}")
        print(f"Strategy rejected: {c['strategy_rejected']} | Risk rejected: {c['risk_rejected']} | Execution rejected: {c['execution_rejected']} | Errors: {c['errors']}")
        print(f"\n[REPORT] state/last_scan_report.json")
