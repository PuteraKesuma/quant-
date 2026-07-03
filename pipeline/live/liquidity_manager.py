"""Liquidity order manager — places & CONTINUOUSLY re-prices a REAL pending LIMIT order at the
Supertrend band (a 'liquidity' resting order), directly via MT5. The EA is market-only and is
NEVER modified, so this brain-side manager owns the pending order for magic 920625.

Behaviour (matches the validated candidate, research/supertrend_validate13.py, $13/$26 stop):
  - UPTREND  -> a BUY_LIMIT rests at the Supertrend SUPPORT band (below price); fills on a pullback.
  - DOWNTREND-> a SELL_LIMIT rests at the RESISTANCE band (above price); fills on a bounce.
  - Each poll the band moves -> MODIFY the pending to the newest band ('limit follows the signal').
  - Trend flips -> CANCEL the pending (and place the opposite side).
  - ONE position: while a position (magic) is open, cancel pendings and wait for the broker SL/TP.
  - Fills are confirmed by Claude ASYNC via the advisor (920625 already in advisor.watch).

SAFETY: `dry_run` (default true) logs every place/modify/cancel WITHOUT sending, so the order
logic is verified before any live order. Flip `dry_run: false` in the slot params to go live
(demo). Respects the broker's minimum stop distance; fail-safe (never crashes the loop).
Run:  python -m pipeline.live.liquidity_manager
"""
import time
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from ..fetch.base_fetcher import load_config
from .data import DataProvider

CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"


class LiquidityManager:
    def __init__(self, cfg: dict, spec: dict):
        p = spec.get("params", {})
        self.cfg = cfg
        self.symbol = spec["symbol"]
        self.mt5_symbol = cfg["symbols"][self.symbol]["mt5_symbol"]
        self.magic = int(spec["magic"])
        self.lot = float(spec["lot"])
        self.timeframe = str(p.get("timeframe", "1h"))
        self.st_period = int(p.get("st_period", 21))
        self.st_mult = float(p.get("st_mult", 5.5))
        self.tp_d = float(p.get("tp_dollars", 26.0))
        self.sl_d = float(p.get("sl_dollars", 13.0))
        self.both_sides = bool(p.get("both_sides", True))
        self.history_bars = int(p.get("history_bars", 15000))
        self.poll = int(p.get("manager_poll_seconds", 10))
        self.dry_run = bool(p.get("dry_run", True))
        self.min_move = float(p.get("min_reprice_move", 0.3))   # only MODIFY if band moved > this
        self.data = DataProvider(cfg)

    # ---------------------------------------------------------------- signal
    def _supertrend(self, h):
        hh = h["high"].values; ll = h["low"].values; cc = h["close"].values
        n = self.st_period
        pc = np.roll(cc, 1); pc[0] = cc[0]
        tr = np.maximum(hh - ll, np.maximum(np.abs(hh - pc), np.abs(ll - pc)))
        atr = np.full(len(cc), np.nan)
        if len(cc) >= n:
            atr[n - 1] = tr[:n].mean()
            for i in range(n, len(cc)):
                atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
        src = (hh + ll) / 2.0
        up = np.full(len(cc), np.nan); dn = np.full(len(cc), np.nan); trend = np.ones(len(cc), int)
        for i in range(len(cc)):
            if not np.isfinite(atr[i]):
                up[i] = src[i]; dn[i] = src[i]; trend[i] = 1; continue
            bu = src[i] - self.st_mult * atr[i]; bd = src[i] + self.st_mult * atr[i]
            up1 = up[i - 1] if i > 0 and np.isfinite(up[i - 1]) else bu
            dn1 = dn[i - 1] if i > 0 and np.isfinite(dn[i - 1]) else bd
            up[i] = max(bu, up1) if (i > 0 and cc[i - 1] > up1) else bu
            dn[i] = min(bd, dn1) if (i > 0 and cc[i - 1] < dn1) else bd
            t = trend[i - 1] if i > 0 else 1
            if t == -1 and cc[i] > dn1:
                t = 1
            elif t == 1 and cc[i] < up1:
                t = -1
            trend[i] = t
        return up, dn, trend       # full arrays (caller checks flatness of the last 3 bars)

    def _band_trend(self):
        df = self.data.recent_bars(self.symbol, self.history_bars)
        if df.empty:
            return None
        h = (df.resample(self.timeframe)
               .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
               .dropna(subset=["open"]))
        if len(h) < self.st_period + 11:
            return None
        now = pd.Timestamp.utcnow(); cb = now.floor(self.timeframe)
        completed = h.iloc[:-1] if (h.index[-1] == cb and len(h) > 1) else h
        up, dn, trend = self._supertrend(completed)
        tr = int(trend[-1])
        # The resting limit persists at the LAST FLAT band level in the CURRENT trend segment
        # (Pine lastLongLimit/lastShortLimit). Scan back from the end, stop at the trend change,
        # take the newest bar where the band was flat 3 bars. Restart-robust (rebuilt from history).
        last_long = last_short = None
        if tr == 1:
            for i in range(len(up) - 1, 1, -1):
                if trend[i] != 1:
                    break
                if up[i] == up[i - 1] == up[i - 2]:
                    last_long = float(up[i]); break
        elif tr == -1:
            for i in range(len(dn) - 1, 1, -1):
                if trend[i] != -1:
                    break
                if dn[i] == dn[i - 1] == dn[i - 2]:
                    last_short = float(dn[i]); break
        return float(up[-1]), float(dn[-1]), tr, last_long, last_short

    # ---------------------------------------------------------------- MT5 ops
    def _send(self, mt5, req, what):
        if self.dry_run:
            logger.info(f"[liqmgr] DRY-RUN {what}: {self._fmt(req)}")
            return True
        r = mt5.order_send(req)
        ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
        logger.info(f"[liqmgr] {what}: {self._fmt(req)} -> retcode={getattr(r,'retcode',None)} {'OK' if ok else 'FAIL '+str(getattr(r,'comment',''))}")
        return ok

    @staticmethod
    def _fmt(req):
        keys = ("type", "price", "sl", "tp", "order")
        return " ".join(f"{k}={req[k]}" for k in keys if k in req)

    def _place(self, mt5, otype, price, sl, tp):
        req = {"action": mt5.TRADE_ACTION_PENDING, "symbol": self.mt5_symbol, "volume": self.lot,
               "type": otype, "price": round(price, 2), "sl": round(sl, 2), "tp": round(tp, 2),
               "magic": self.magic, "type_time": mt5.ORDER_TIME_GTC,
               "comment": "liqlimit"}
        self._send(mt5, req, "PLACE " + ("BUY_LIMIT" if otype == mt5.ORDER_TYPE_BUY_LIMIT else "SELL_LIMIT"))

    def _modify(self, mt5, ticket, price, sl, tp):
        req = {"action": mt5.TRADE_ACTION_MODIFY, "order": ticket, "price": round(price, 2),
               "sl": round(sl, 2), "tp": round(tp, 2), "type_time": mt5.ORDER_TIME_GTC}
        self._send(mt5, req, "MODIFY (re-price limit)")

    def _cancel(self, mt5, ticket):
        self._send(mt5, {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}, "CANCEL pending")

    # ---------------------------------------------------------------- loop
    def poll_once(self, mt5) -> None:
        bt = self._band_trend()
        if bt is None:
            return
        up_band, dn_band, trend, last_long, last_short = bt   # last_long/short = last FLAT band level
        poss = mt5.positions_get(symbol=self.mt5_symbol)
        pends = mt5.orders_get(symbol=self.mt5_symbol)
        if poss is None or pends is None:            # MT5 not ready -> skip
            return
        my_pos = [p for p in poss if p.magic == self.magic]
        my_pend = [o for o in pends if o.magic == self.magic]

        if my_pos:                                    # ONE position -> cancel pendings, let broker manage
            for o in my_pend:
                self._cancel(mt5, o.ticket)
            return

        # rest the limit at the LAST FLAT band level (Pine longPrice=up / shortPrice=dn on flat).
        if trend == 1 and last_long is not None:
            otype = mt5.ORDER_TYPE_BUY_LIMIT; price = last_long
            sl = price - self.sl_d; tp = price + self.tp_d
        elif trend == -1 and self.both_sides and last_short is not None:
            otype = mt5.ORDER_TYPE_SELL_LIMIT; price = last_short
            sl = price + self.sl_d; tp = price - self.tp_d
        else:
            for o in my_pend:
                self._cancel(mt5, o.ticket)          # no flat level in this trend -> no resting limit
            return

        # guard: limit must be on the correct side of the market by >= stops_level
        tick = mt5.symbol_info_tick(self.mt5_symbol)
        info = mt5.symbol_info(self.mt5_symbol)
        if tick and info:
            min_dist = info.trade_stops_level * info.point
            bid, ask = tick.bid, tick.ask
            if otype == mt5.ORDER_TYPE_BUY_LIMIT and price >= ask - min_dist:
                logger.info(f"[liqmgr] band {price:.2f} too close to ask {ask:.2f} (min {min_dist}) -> wait")
                return
            if otype == mt5.ORDER_TYPE_SELL_LIMIT and price <= bid + min_dist:
                logger.info(f"[liqmgr] band {price:.2f} too close to bid {bid:.2f} -> wait")
                return

        same = [o for o in my_pend if o.type == otype]
        wrong = [o for o in my_pend if o.type != otype]
        for o in wrong:
            self._cancel(mt5, o.ticket)               # trend flipped -> drop the opposite-side limit
        if not same:
            self._place(mt5, otype, price, sl, tp)
        elif abs(same[0].price_open - price) > self.min_move:
            self._modify(mt5, same[0].ticket, price, sl, tp)   # band moved -> follow it

    def run(self) -> None:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            logger.error(f"[liqmgr] MT5 init failed: {mt5.last_error()}"); return
        logger.info(f"[liqmgr] up. magic={self.magic} {self.symbol} tf={self.timeframe} "
                    f"SL/TP=${self.sl_d}/${self.tp_d} dry_run={self.dry_run} poll={self.poll}s")
        try:
            while True:
                try:
                    self.poll_once(mt5)
                except Exception:
                    logger.exception("[liqmgr] poll error (continuing)")
                time.sleep(self.poll)
        finally:
            mt5.shutdown()


def main() -> None:
    cfg = load_config()
    specs = [s for s in cfg["live"]["strategies"] if s.get("type") == "liqlimit"]
    if not specs:
        logger.info("[liqmgr] no liqlimit slot in config. Exiting."); return
    LiquidityManager(cfg, specs[0]).run()


if __name__ == "__main__":
    main()
