"""ORB stop manager — rests a REAL pending STOP order at the opening-range boundary so the
NAS100 ORB entry fills AT the breakout level instead of at the next M1 close (the market EA
slipped +4.75..+16.30 pts per trade; audit research/signal_logic_audit.py). The EA is market-
only and is NEVER modified, so this brain-side manager owns the pending STOP for magic 920617.

Faithful port of the LIVE ORB logic (pipeline/live/signal.ORBStrategy), so the SAME trades fire,
only with a clean entry:
  - Opening range = [NY open, +range_minutes) of today's M1 (DST-aware open, like dst_open).
  - Daily-SMA(trend_sma) trend gate: only the trend-side breakout is tradeable; if the COUNTER-
    trend boundary breaks FIRST, the session is dead (matches generate_signals[0] + TRENDFILTER).
  - ONE trade per session (checked against today's filled deals -> restart-robust).
  - Trend UP  -> BUY_STOP  at range_high, sl=range_low,  tp=range_high+range_size.
  - Trend DOWN-> SELL_STOP at range_low,  sl=range_high, tp=range_low -range_size.
  - If price is already BEYOND the boundary when we place (late deploy) -> MARKET fallback (no
    worse than the EA today), unless the hypothetical trade would already have exited.
  - breakeven_r: once the trade runs >= breakeven_r*risk in favour, move the broker SL to entry
    (the EA keeps ORB OUT of its own breakeven, so this manager owns it).
  - session_end_utc: flat by time -> close any open position at market and cancel stops.

SAFETY: `dry_run` (default true) logs every place/modify/cancel/close WITHOUT sending, so the
logic is verified before any live order. Flip `dry_run: false` in the slot params to go live
(demo). Fail-safe: a poll error never crashes the loop.
Run:  python -m pipeline.live.orb_stop_manager
"""
import time
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from loguru import logger

from ..fetch.base_fetcher import load_config
from .data import DataProvider

CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"


class OrbStopManager:
    def __init__(self, cfg: dict, spec: dict):
        p = spec.get("params", {})
        oc = cfg["orb"]
        self.cfg = cfg
        self.symbol = spec["symbol"]
        self.mt5_symbol = cfg["symbols"][self.symbol]["mt5_symbol"]
        self.magic = int(spec["magic"])
        self.lot = float(spec["lot"])
        self.session = spec["session"]
        sess = cfg["symbols"][self.symbol]["sessions"][self.session]
        self.open_h, self.open_m = map(int, sess["open"].split(":"))
        self.dst_open = bool(p.get("dst_open", False))
        self.range_minutes = int(p.get("range_minutes", oc["range_minutes"]))
        self.tp_mult = float(p.get("tp_mult", oc["tp_multiplier"]))
        self.sl_mult = float(p.get("sl_mult", oc["sl_multiplier"]))
        self.use_sl = bool(p.get("use_sl", True))
        self.entry_buffer = oc["entry_buffer_pips"] * cfg["symbols"][self.symbol]["pip_size"]
        self.trend_sma = p.get("trend_sma")                       # daily-SMA trend gate (None = off)
        self.breakeven_r = p.get("breakeven_r")                   # SL->entry once +Xr favourable
        self.session_end = p.get("session_end_utc")               # "HH:MM" time-exit (UTC)
        self.recent_bars = int(cfg["live"].get("recent_bars", 600))
        self.poll = int(p.get("manager_poll_seconds", 10))
        self.dry_run = bool(p.get("dry_run", True))
        self.price_tol = float(p.get("stop_price_tol", 0.5))      # don't re-place if within this
        self.data = DataProvider(cfg)
        self._trend_cache: dict = {}

    # ---------------------------------------------------------------- helpers
    def _server_offset(self, mt5) -> int:
        """Broker server time minus UTC in whole hours (FBS = +3 summer). Config override
        or auto-detect from a fresh tick. Only used to place deal timestamps back on UTC."""
        cfgv = self.cfg.get("live", {}).get("mt5_server_utc_offset_hours")
        if cfgv is not None:
            return int(cfgv)
        tick = mt5.symbol_info_tick(self.mt5_symbol)
        if tick and tick.time:
            diff = (pd.Timestamp(tick.time, unit="s", tz="UTC") - pd.Timestamp.utcnow()).total_seconds() / 3600.0
            nn = round(diff)
            if abs(diff - nn) <= 0.5 and -12 <= nn <= 14:
                return int(nn)
        return 0

    def _open_time(self, today):
        """DST-aware NY open (h, m). The configured open is the US-summer (DST) UTC open;
        under US standard time (winter) the equity cash open is 1h later. Mirrors
        ORBStrategy._dst_adjust_open."""
        h, m = self.open_h, self.open_m
        if self.dst_open:
            import datetime as _dt
            et = _dt.datetime(int(today.year), int(today.month), int(today.day), 12,
                              tzinfo=ZoneInfo("America/New_York"))
            if et.dst() == timedelta(0):                          # standard time -> 1h later
                total = h * 60 + m + 60
                h, m = total // 60, total % 60
        return h, m

    def _trend_dir(self, mt5, today) -> int:
        """+1/-1/0 = sign(last completed daily close - SMA(trend_sma)). Cached once/day.
        0 (and any error) blocks the session (fail-safe). Mirrors ORBStrategy._trend_dir."""
        if not self.trend_sma:
            return 1                                              # no gate -> allow (unused path)
        n = int(self.trend_sma)
        key = (today, n)
        if key in self._trend_cache:
            return self._trend_cache[key]
        direction = 0
        try:
            rates = mt5.copy_rates_from_pos(self.mt5_symbol, mt5.TIMEFRAME_D1, 0, n + 5)
            if rates is not None and len(rates) > n:
                closes = pd.Series(rates["close"], dtype=float).iloc[:-1]   # drop forming day
                if len(closes) >= n:
                    sma = float(closes.tail(n).mean())
                    last = float(closes.iloc[-1])
                    direction = 1 if last > sma else (-1 if last < sma else 0)
        except Exception as e:
            logger.warning(f"[orbmgr] trend_dir unavailable: {e}")
        self._trend_cache[key] = direction
        logger.info(f"[orbmgr] daily trend(SMA{n}) dir = {direction}")
        return direction

    @staticmethod
    def _first_breakout(post, orb_high, orb_low, buf):
        """Side ('long'/'short') and ts of the FIRST post-range boundary break, or (None, None).
        High checked before low per bar (matches generate_signals)."""
        for ts, bar in post.iterrows():
            if bar["high"] > orb_high + buf:
                return "long", ts
            if bar["low"] < orb_low - buf:
                return "short", ts
        return None, None

    def _already_exited(self, df, entry_ts, side, entry, sl, tp) -> bool:
        """Has the hypothetical trade already hit SL/TP/BE since entry_ts? Ports
        ORBStrategy._exit_hit (SL checked before TP; breakeven arms at +breakeven_r*risk)."""
        post = df[df.index >= entry_ts]
        if post.empty:
            return False
        risk = abs(entry - sl)
        armed = False
        for _, bar in post.iterrows():
            if side == "long":
                if self.breakeven_r is not None and not armed and (bar["high"] - entry) >= self.breakeven_r * risk:
                    armed = True
                if armed and bar["low"] <= entry:
                    return True
                if self.use_sl and bar["low"] <= sl:
                    return True
                if bar["high"] >= tp:
                    return True
            else:
                if self.breakeven_r is not None and not armed and (entry - bar["low"]) >= self.breakeven_r * risk:
                    armed = True
                if armed and bar["high"] >= entry:
                    return True
                if self.use_sl and bar["high"] >= sl:
                    return True
                if bar["low"] <= tp:
                    return True
        return False

    def _traded_today(self, mt5, range_start_utc, offset) -> bool:
        """True if an ENTRY deal for this magic already fired this session (>= range_start).
        Restart-robust one-trade-per-session guard. Deal times are server-labelled -> back to UTC."""
        frm = (pd.Timestamp.utcnow() - pd.Timedelta(days=2)).to_pydatetime()
        to = (pd.Timestamp.utcnow() + pd.Timedelta(days=1)).to_pydatetime()
        for d in (mt5.history_deals_get(frm, to) or []):
            if d.magic != self.magic or d.entry != 0:
                continue
            t_utc = pd.Timestamp(d.time, unit="s", tz="UTC") - pd.Timedelta(hours=offset)
            if t_utc >= range_start_utc:
                return True
        return False

    # ---------------------------------------------------------------- MT5 ops
    def _send(self, mt5, req, what):
        if self.dry_run:
            logger.info(f"[orbmgr] DRY-RUN {what}: {self._fmt(req)}")
            return True
        r = mt5.order_send(req)
        ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
        logger.info(f"[orbmgr] {what}: {self._fmt(req)} -> retcode={getattr(r,'retcode',None)} "
                    f"{'OK' if ok else 'FAIL '+str(getattr(r,'comment',''))}")
        return ok

    @staticmethod
    def _fmt(req):
        keys = ("type", "price", "sl", "tp", "order", "position", "volume")
        return " ".join(f"{k}={req[k]}" for k in keys if k in req)

    def _filling(self, mt5, info):
        fm = info.filling_mode
        if fm & 2:                                                # SYMBOL_FILLING_IOC
            return mt5.ORDER_FILLING_IOC
        if fm & 1:                                                # SYMBOL_FILLING_FOK
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _place_stop(self, mt5, otype, price, sl, tp):
        req = {"action": mt5.TRADE_ACTION_PENDING, "symbol": self.mt5_symbol, "volume": self.lot,
               "type": otype, "price": round(price, 2), "sl": round(sl, 2), "tp": round(tp, 2),
               "magic": self.magic, "type_time": mt5.ORDER_TIME_GTC, "comment": "orb30_nas"}
        self._send(mt5, req, "PLACE " + ("BUY_STOP" if otype == mt5.ORDER_TYPE_BUY_STOP else "SELL_STOP"))

    def _market(self, mt5, is_buy, sl, tp):
        info = mt5.symbol_info(self.mt5_symbol)
        tick = mt5.symbol_info_tick(self.mt5_symbol)
        price = tick.ask if is_buy else tick.bid
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": self.mt5_symbol, "volume": self.lot,
               "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
               "price": price, "sl": round(sl, 2), "tp": round(tp, 2), "magic": self.magic,
               "type_filling": self._filling(mt5, info), "comment": "orb30_nas_mkt"}
        self._send(mt5, req, "MARKET " + ("BUY" if is_buy else "SELL") + " (late fallback)")

    def _modify_sl(self, mt5, pos, new_sl):
        req = {"action": mt5.TRADE_ACTION_SLTP, "symbol": self.mt5_symbol,
               "position": pos.ticket, "sl": round(new_sl, 2), "tp": pos.tp}
        self._send(mt5, req, "MODIFY SL->entry (breakeven)")

    def _close(self, mt5, pos):
        info = mt5.symbol_info(self.mt5_symbol)
        tick = mt5.symbol_info_tick(self.mt5_symbol)
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": self.mt5_symbol, "volume": pos.volume,
               "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
               "position": pos.ticket, "price": tick.bid if is_buy else tick.ask,
               "type_filling": self._filling(mt5, info), "magic": self.magic, "comment": "orb_session_end"}
        self._send(mt5, req, "CLOSE at session_end")

    def _cancel(self, mt5, ticket):
        self._send(mt5, {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}, "CANCEL pending")

    def _cancel_all(self, mt5, my_pend):
        for o in my_pend:
            self._cancel(mt5, o.ticket)

    # ---------------------------------------------------------------- loop
    def poll_once(self, mt5) -> None:
        now = pd.Timestamp.utcnow()
        today = now.normalize()
        h, m = self._open_time(today)
        range_start = today.replace(hour=h, minute=m)
        range_end = range_start + pd.Timedelta(minutes=self.range_minutes)
        offset = self._server_offset(mt5)

        poss = mt5.positions_get(symbol=self.mt5_symbol)
        pends = mt5.orders_get(symbol=self.mt5_symbol)
        if poss is None or pends is None:                         # MT5 not ready -> skip this poll
            return
        my_pos = [p for p in poss if p.magic == self.magic]
        my_pend = [o for o in pends if o.magic == self.magic]

        session_end_ts = None
        if self.session_end:
            eh, em = map(int, self.session_end.split(":"))
            session_end_ts = today.replace(hour=eh, minute=em)

        # --- an open position: manage breakeven + session-end close; cancel any leftover stops ---
        if my_pos:
            pos = my_pos[0]
            self._cancel_all(mt5, my_pend)                        # in the trade -> no resting stops
            if session_end_ts is not None and now >= session_end_ts:
                self._close(mt5, pos)
                return
            if self.breakeven_r is not None:
                self._manage_breakeven(mt5, pos, offset)
            return

        # --- flat before the range completes: fresh slate (drop any stale stop) ---
        if now < range_end:
            if my_pend:
                self._cancel_all(mt5, my_pend)
            return

        # --- flat after session end: nothing more today ---
        if session_end_ts is not None and now >= session_end_ts:
            self._cancel_all(mt5, my_pend)
            return

        # --- one trade per session: if a deal already fired today, we're done ---
        if self._traded_today(mt5, range_start, offset):
            self._cancel_all(mt5, my_pend)
            return

        # --- compute today's opening range from M1 (UTC index) ---
        df = self.data.recent_bars(self.symbol, self.recent_bars)
        df = df[df.index.normalize() == today]
        win = df[(df.index >= range_start) & (df.index < range_end)]
        if len(win) < self.range_minutes // 2:                   # not enough range bars -> skip
            self._cancel_all(mt5, my_pend)
            return
        orb_high = float(win["high"].max())
        orb_low = float(win["low"].min())
        orb_size = orb_high - orb_low
        if orb_size <= 0:
            self._cancel_all(mt5, my_pend)
            return

        # --- daily-SMA trend gate: pick the tradeable side (fail-safe FLAT on 0/error) ---
        tdir = self._trend_dir(mt5, today) if self.trend_sma else 0
        if self.trend_sma and tdir == 0:
            self._cancel_all(mt5, my_pend)
            return
        side = "long" if tdir >= 1 else "short"                  # trend_sma always set for ORB

        # --- first breakout must be the trend side; counter-trend first = dead session ---
        post = df[df.index >= range_end]
        fb_side, fb_ts = self._first_breakout(post, orb_high, orb_low, self.entry_buffer)
        if fb_side is not None and fb_side != side:
            self._cancel_all(mt5, my_pend)                       # TRENDFILTER: first break was counter-trend
            return

        # --- trade geometry (identical to the backtest/live fill) ---
        if side == "long":
            entry = orb_high + self.entry_buffer
            sl = entry - orb_size * self.sl_mult
            tp = entry + orb_size * self.tp_mult
            otype = mt5.ORDER_TYPE_BUY_STOP
        else:
            entry = orb_low - self.entry_buffer
            sl = entry + orb_size * self.sl_mult
            tp = entry - orb_size * self.tp_mult
            otype = mt5.ORDER_TYPE_SELL_STOP
        if not self.use_sl:
            sl = 0.0

        # --- if the trend-side already broke, don't chase a trade that has since exited ---
        if fb_side == side and self._already_exited(df, fb_ts, side, entry, sl, tp):
            self._cancel_all(mt5, my_pend)
            return

        # --- drop any wrong-side leftover, then rest / market the trend-side entry ---
        for o in my_pend:
            if o.type != otype:
                self._cancel(mt5, o.ticket)
        same = [o for o in my_pend if o.type == otype]

        tick = mt5.symbol_info_tick(self.mt5_symbol)
        info = mt5.symbol_info(self.mt5_symbol)
        if tick is None or info is None:
            return
        min_dist = info.trade_stops_level * info.point
        ask, bid = tick.ask, tick.bid

        # price already beyond the boundary (late deploy) -> MARKET catch, else rest the STOP
        if side == "long" and ask >= entry - min_dist:
            self._cancel_all(mt5, same)
            self._market(mt5, True, sl, tp)
            return
        if side == "short" and bid <= entry + min_dist:
            self._cancel_all(mt5, same)
            self._market(mt5, False, sl, tp)
            return

        if not same:
            self._place_stop(mt5, otype, entry, sl, tp)
        elif abs(same[0].price_open - entry) > self.price_tol:   # range shouldn't move, but keep exact
            self._cancel(mt5, same[0].ticket)
            self._place_stop(mt5, otype, entry, sl, tp)

    def _manage_breakeven(self, mt5, pos, offset) -> None:
        """Once the trade has run >= breakeven_r*risk in favour (by M1 highs/lows since entry),
        move the broker SL to entry. One-way; never loosens. Mirrors the signal-driven BE."""
        entry = float(pos.price_open)
        sl = float(pos.sl or 0.0)
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        if sl == entry:
            return                                                # already at breakeven
        # reconstruct risk from the original 1R stop distance (range size)
        # (pos.sl is the entry stop = range_low/high, so risk = |entry - original_sl|)
        risk = abs(entry - sl) if sl > 0 else None
        entry_ts = pd.Timestamp(pos.time, unit="s", tz="UTC") - pd.Timedelta(hours=offset)
        df = self.data.recent_bars(self.symbol, self.recent_bars)
        post = df[df.index >= entry_ts]
        if post.empty or risk is None or risk <= 0:
            return
        if is_buy:
            mfe = float(post["high"].max()) - entry
            if mfe >= self.breakeven_r * risk and (sl < entry):
                self._modify_sl(mt5, pos, entry)
        else:
            mfe = entry - float(post["low"].min())
            if mfe >= self.breakeven_r * risk and (sl > entry):
                self._modify_sl(mt5, pos, entry)

    def run(self) -> None:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            logger.error(f"[orbmgr] MT5 init failed: {mt5.last_error()}"); return
        logger.info(f"[orbmgr] up. magic={self.magic} {self.symbol} range={self.range_minutes}m "
                    f"trend_sma={self.trend_sma} be_r={self.breakeven_r} end={self.session_end} "
                    f"dry_run={self.dry_run} poll={self.poll}s")
        try:
            while True:
                try:
                    self.poll_once(mt5)
                except Exception:
                    logger.exception("[orbmgr] poll error (continuing)")
                time.sleep(self.poll)
        finally:
            mt5.shutdown()


def main() -> None:
    cfg = load_config()
    specs = [s for s in cfg["live"]["strategies"]
             if s.get("type") == "orb" and s.get("params", {}).get("pending_stop")]
    if not specs:
        logger.info("[orbmgr] no ORB slot with params.pending_stop in config. Exiting."); return
    OrbStopManager(cfg, specs[0]).run()


if __name__ == "__main__":
    main()
