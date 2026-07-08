"""Monthly Profit Governor — the 'bersyukur' rule. Once month-to-date REALIZED PnL for the live book
reaches `threshold` x the month's FORECAST target, PAUSE new entries for the rest of the month (open
positions are left to ride to their own broker SL/TP). Resets automatically on the 1st of next month.

Target = forecast: mean weekly $ of the deployed book (research/book_weekly.csv) x weeks-in-month.
(Honest: this is the EXPECTED net, not a promise — returns aren't forecastable; it's an anchor.)

Writes _MONITOR/governor.json {month, paused, mtd, target, trigger}. Two consumers read it and stop
opening NEW positions while paused: the brain (pipeline/live/signal.py: Z + Golden) and the ORB
manager (pipeline/live/orb_stop_manager.py). Sticky within a month once tripped.
Run: python -m pipeline.live.monthly_governor
"""
import time
import json
import calendar
from datetime import timedelta
from pathlib import Path

import pandas as pd
from loguru import logger

from ..fetch.base_fetcher import load_config

STATE = Path(r"C:\Quant\_MONITOR\governor.json")
BOOK_CSV = Path(r"C:\Quant\research\book_weekly.csv")


class MonthlyGovernor:
    def __init__(self, cfg: dict):
        g = cfg.get("governor", {})
        self.magics = set(g.get("magics", [920617, 920622, 920626]))
        self.threshold = float(g.get("threshold", 0.75))
        self.poll = int(g.get("poll_seconds", 300))
        self.target_override = g.get("target_usd")          # None -> from forecast
        self.cfg = cfg

    def _offset(self, mt5) -> int:
        v = self.cfg.get("live", {}).get("mt5_server_utc_offset_hours")
        if v is not None:
            return int(v)
        t = mt5.symbol_info_tick("XAUUSD")
        if t and t.time:
            diff = (pd.Timestamp(t.time, unit="s", tz="UTC") - pd.Timestamp.utcnow()).total_seconds() / 3600.0
            n = round(diff)
            if abs(diff - n) <= 0.5 and -12 <= n <= 14:
                return int(n)
        return 0

    def _monthly_target(self, now) -> float:
        if self.target_override:
            return float(self.target_override)
        try:
            mean_wk = float(pd.read_csv(BOOK_CSV, index_col=0)["pnl"].mean())
        except Exception:
            mean_wk = 20.0
        days = calendar.monthrange(now.year, now.month)[1]
        return mean_wk * (days / 7.0)

    def _mtd(self, mt5, now, off) -> float:
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        frm = (month_start - timedelta(days=2)).to_pydatetime()
        to = (now + timedelta(days=1)).to_pydatetime()
        tot = 0.0
        for d in (mt5.history_deals_get(frm, to) or []):
            if d.magic in self.magics and d.entry == 1:            # realized (exit) deals
                t_utc = pd.Timestamp(d.time, unit="s", tz="UTC") - pd.Timedelta(hours=off)
                if t_utc >= month_start:
                    tot += d.profit + d.swap + d.commission
        return float(tot)

    def poll_once(self, mt5) -> None:
        now = pd.Timestamp.now("UTC")
        month = now.strftime("%Y-%m")
        prev = {}
        if STATE.exists():
            try:
                prev = json.loads(STATE.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
        sticky = bool(prev.get("paused", False)) and prev.get("month") == month   # stay paused this month
        off = self._offset(mt5)
        mtd = self._mtd(mt5, now, off)
        target = self._monthly_target(now)
        trigger = self.threshold * target
        paused = sticky or (target > 0 and mtd >= trigger)
        state = {"month": month, "paused": bool(paused), "mtd": round(mtd, 2),
                 "target": round(target, 2), "trigger": round(trigger, 2),
                 "threshold": self.threshold, "updated": now.isoformat()}
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        logger.info(f"[governor] {month} MTD ${mtd:+.2f} / target ${target:.0f} "
                    f"(stop@75%=${trigger:.0f}) -> {'PAUSED (bersyukur)' if paused else 'active'}")

    def run(self) -> None:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            logger.error(f"[governor] MT5 init failed: {mt5.last_error()}"); return
        logger.info(f"[governor] up. magics={sorted(self.magics)} threshold={self.threshold} poll={self.poll}s")
        try:
            while True:
                try:
                    self.poll_once(mt5)
                except Exception:
                    logger.exception("[governor] poll error (continuing)")
                time.sleep(self.poll)
        finally:
            mt5.shutdown()


def main() -> None:
    MonthlyGovernor(load_config()).run()


if __name__ == "__main__":
    main()
