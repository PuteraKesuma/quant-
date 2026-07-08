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
        self.mode = str(g.get("mode", "profit"))            # 'profit' (bersyukur) | 'rules' (prop)
        self.threshold = float(g.get("threshold", 0.75))
        self.poll = int(g.get("poll_seconds", 300))
        self.target_override = g.get("target_usd")          # None -> from forecast
        # --- rules mode (WMT funded) ---
        self.max_risk = float(g.get("max_risk_per_trade", 90))
        self.daily_loss = float(g.get("daily_loss_usd", 500))
        self.daily_buffer = float(g.get("daily_stop_buffer", 60))
        self.maxloss_floor = float(g.get("maxloss_floor", 0))
        self.maxloss_buffer = float(g.get("maxloss_buffer", 150))
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
        """Month-to-date realized PnL, attributed by the POSITION's ENTRY magic (not the closing
        deal's magic, which can be wrong — MT5 tags a close with the EA's last-set magic). Wide
        lookback captures entries of positions that opened earlier but closed this month."""
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        frm = (month_start - timedelta(days=60)).to_pydatetime()
        to = (now + timedelta(days=1)).to_pydatetime()
        deals = mt5.history_deals_get(frm, to) or []
        entry_magic = {d.position_id: d.magic for d in deals if d.entry == 0}   # position -> true magic
        tot = 0.0
        for d in deals:
            if d.entry != 1:                                        # realized (exit) deals only
                continue
            mg = entry_magic.get(d.position_id, d.magic)            # attribute by entry magic
            if mg not in self.magics:
                continue
            t_utc = pd.Timestamp(d.time, unit="s", tz="UTC") - pd.Timedelta(hours=off)
            if t_utc >= month_start:
                tot += d.profit + d.swap + d.commission
        return float(tot)

    def _today_realized(self, mt5, now, off) -> float:
        """Today's realized PnL (book), attributed by entry magic (robust to close-deal mis-tag)."""
        day_start = now.normalize()
        frm = (day_start - timedelta(days=2)).to_pydatetime()
        to = (now + timedelta(days=1)).to_pydatetime()
        deals = mt5.history_deals_get(frm, to) or []
        entry_magic = {d.position_id: d.magic for d in deals if d.entry == 0}
        tot = 0.0
        for d in deals:
            if d.entry != 1:
                continue
            if entry_magic.get(d.position_id, d.magic) not in self.magics:
                continue
            t_utc = pd.Timestamp(d.time, unit="s", tz="UTC") - pd.Timedelta(hours=off)
            if t_utc >= day_start:
                tot += d.profit + d.swap + d.commission
        return float(tot)

    def _flatten(self, mt5) -> None:
        """Close all book positions at market (max-loss protection)."""
        for p in (mt5.positions_get() or []):
            if p.magic not in self.magics:
                continue
            info = mt5.symbol_info(p.symbol); tick = mt5.symbol_info_tick(p.symbol)
            if not info or not tick:
                continue
            fm = info.filling_mode
            fill = mt5.ORDER_FILLING_IOC if (fm & 2) else (mt5.ORDER_FILLING_FOK if (fm & 1) else mt5.ORDER_FILLING_RETURN)
            is_buy = p.type == mt5.POSITION_TYPE_BUY
            req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": p.symbol, "volume": p.volume,
                   "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY, "position": p.ticket,
                   "price": tick.bid if is_buy else tick.ask, "type_filling": fill, "magic": p.magic,
                   "comment": "governor maxloss flatten"}
            r = mt5.order_send(req)
            logger.info(f"[governor] FLATTEN {p.symbol} {p.volume} -> retcode={getattr(r,'retcode',None)}")

    def _rules_poll(self, mt5) -> None:
        now = pd.Timestamp.now("UTC")
        day = now.strftime("%Y-%m-%d")
        off = self._offset(mt5)
        ai = mt5.account_info()
        equity = float(ai.equity) if ai else 0.0
        today = self._today_realized(mt5, now, off)
        prev = {}
        if STATE.exists():
            try:
                prev = json.loads(STATE.read_text(encoding="utf-8"))
            except Exception:
                prev = {}
        daily_stop_at = -(self.daily_loss - self.daily_buffer)
        maxloss_stop_at = self.maxloss_floor + self.maxloss_buffer
        sticky_day = bool(prev.get("paused_daily")) and prev.get("day") == day
        sticky_max = bool(prev.get("paused_maxloss"))
        paused_daily = sticky_day or (today <= daily_stop_at)
        maxloss_trip = equity > 0 and self.maxloss_floor > 0 and equity <= maxloss_stop_at
        paused_maxloss = sticky_max or maxloss_trip
        if maxloss_trip and not sticky_max:
            self._flatten(mt5)                              # protect the account
        paused = paused_daily or paused_maxloss
        reason = "maxloss" if paused_maxloss else ("daily" if paused_daily else "")
        state = {"mode": "rules", "day": day, "paused": bool(paused), "reason": reason,
                 "today_realized": round(today, 2), "equity": round(equity, 2),
                 "daily_stop_at": round(daily_stop_at, 2), "maxloss_stop_at": round(maxloss_stop_at, 2),
                 "paused_daily": bool(paused_daily), "paused_maxloss": bool(paused_maxloss),
                 "max_risk_per_trade": self.max_risk, "magics": sorted(self.magics),
                 "updated": now.isoformat()}
        STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
        logger.info(f"[governor:rules] {day} today ${today:+.2f} (stop ${daily_stop_at:.0f}) "
                    f"equity ${equity:.0f} (floor+buf ${maxloss_stop_at:.0f}) -> "
                    f"{'PAUSED '+reason if paused else 'active'}")

    def poll_once(self, mt5) -> None:
        if self.mode == "rules":
            return self._rules_poll(mt5)
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
