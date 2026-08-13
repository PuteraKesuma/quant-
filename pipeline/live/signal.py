"""Signal engine: a pluggable registry of strategy "slots".

Each slot in `config.yaml live.strategies` becomes one independent strategy that
emits a desired-state `SignalResponse`. The server returns all slots for a symbol
as a list, so multiple models can run concurrently (each tagged by its own
`magic`). Adding a new model = add a class to `STRATEGY_TYPES` + a config entry;
the EA never changes.

Idempotency: `action` is the position the slot *should hold*; `signal_id` is stable
for the life of one signal. The EA acts only when `signal_id` changes, and the
broker's SL/TP closes the trade (the EA won't reopen â€” the signal_id was acted on).
"""
import pandas as pd
from loguru import logger

from ..fetch.base_fetcher import load_config
from ..backtest.strategy_orb import ORBParams, generate_signals
from ..backtest.strategy_zrev import ZRevParams, resample_1h
from ..vision.analyzer import VisionAnalyzer
from ..vision.capture import ChartCapturer
from ..vision.journal import VisionJournal
from ..vision.state import SlotState
from .contracts import SignalResponse, flat
from .data import DataProvider

_DUMMY_CYCLE = ["FLAT", "BUY", "FLAT", "SELL"]  # one phase per minute

# --- Monthly Profit Governor gate (the 'bersyukur' rule). When _MONITOR/governor.json is paused,
#     strategies may only HOLD or EXIT â€” no NEW entry (FLAT->BUY/SELL) and no reversal. Open
#     positions ride to their broker SL/TP. See pipeline/live/monthly_governor.py. ---
import json as _json
from pathlib import Path as _Path
_GOV_STATE = _Path(r"C:\Quant\_MONITOR\governor.json")


def _entries_paused() -> bool:
    try:
        return bool(_json.loads(_GOV_STATE.read_text(encoding="utf-8")).get("paused", False))
    except Exception:
        return False


def _governed(action: str, prev: str) -> str:
    """Block new entries/reversals while the governor is paused; allow hold and exit."""
    if not _entries_paused():
        return action
    if prev == "FLAT":
        return "FLAT" if action in ("BUY", "SELL") else action        # no new entry
    if action in ("BUY", "SELL") and action != prev:
        return "FLAT"                                                  # reversal -> just exit
    return action


_CFG_CACHE = None


def _cfg() -> dict:
    """Cached config.yaml (for fail-closed cap + book magics). {} on any error."""
    global _CFG_CACHE
    if _CFG_CACHE is None:
        try:
            _CFG_CACHE = load_config()
        except Exception:
            _CFG_CACHE = {}
    return _CFG_CACHE


def _config_cap() -> float:
    try:
        return float((_cfg().get("governor") or {}).get("max_risk_per_trade", 0.0) or 0.0)
    except Exception:
        return 0.0


def _risk_cap() -> float:
    """Per-trade $ risk cap. Prefer the LIVE governor state; if governor.json is missing/unreadable,
    FAIL CLOSED to the config default so a downed governor never silently removes the cap
    (the 2026-07 WMT bug: governor died -> cap became 0 -> an over-limit trade slipped through)."""
    try:
        v = _json.loads(_GOV_STATE.read_text(encoding="utf-8")).get("max_risk_per_trade", None)
        if v is not None:
            return float(v)
    except Exception:
        pass
    return _config_cap()


def _book_magics() -> set:
    try:
        return {int(m) for m in (_cfg().get("governor") or {}).get("magics", [])}
    except Exception:
        return set()


def _book_conflict(mt5_symbol: str, want_action: str, my_magic) -> bool:
    """Net-exposure guard: True if ANOTHER book slot already holds a SAME-direction position on this
    symbol. Prevents two correlated slots stacking same-side risk (the 2026-07 Z+Golden double-short
    that doubled the drawdown). Fail-OPEN on any MT5 error (a read glitch must not halt the book)."""
    if want_action not in ("BUY", "SELL"):
        return False
    try:
        import MetaTrader5 as mt5
        book = _book_magics()
        for p in (mt5.positions_get(symbol=mt5_symbol) or []):
            if p.magic == my_magic or p.magic not in book:
                continue
            pdir = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
            if pdir == want_action:
                return True
    except Exception as e:
        logger.warning(f"[net-exposure] check failed ({e}); allowing entry")
        return False
    return False


def _risk_ok(entry: float, sl: float, lot: float, mt5_symbol: str) -> bool:
    """True if a new position's $ risk (|entry-sl| * lot * contract_size) is within the cap.
    Enforces the WMT $90/trade rule: a trade whose stop is too wide to size under the cap is SKIPPED."""
    cap = _risk_cap()
    if cap <= 0 or not sl:
        return True
    try:
        import MetaTrader5 as mt5
        info = mt5.symbol_info(mt5_symbol)
        if info is None:
            return True
        return abs(entry - sl) * lot * info.trade_contract_size <= cap
    except Exception:
        return True


class BaseStrategy:
    """One config slot. Subclasses implement `evaluate()`."""

    def __init__(self, spec: dict, cfg: dict, data: DataProvider):
        self.spec = spec
        self.cfg = cfg
        self.data = data
        self.name = spec["name"]
        self.symbol = spec["symbol"]
        self.lot = spec["lot"]
        self.magic = int(spec["magic"])

    def evaluate(self) -> SignalResponse:
        raise NotImplementedError

    def _flat(self, suffix: str, ts: str) -> SignalResponse:
        return flat(self.name, self.symbol, self.magic,
                    f"{self.symbol}-{self.name}-{suffix}", ts)


class DummyStrategy(BaseStrategy):
    """Deterministic 1-minute FLAT->BUY->FLAT->SELL cycle to prove the EA loop."""

    def evaluate(self) -> SignalResponse:
        now = pd.Timestamp.utcnow()
        phase = int(now.value // 60_000_000_000)            # minute index since epoch
        action = _DUMMY_CYCLE[phase % len(_DUMMY_CYCLE)]
        sig_id = f"{self.symbol}-{self.name}-DUMMY-{phase}"
        ts = now.isoformat()

        if action == "FLAT":
            return flat(self.name, self.symbol, self.magic, sig_id, ts)

        price = self._last_price()
        offset = price * 0.001                              # ~0.1% dummy band
        sl, tp = (price - offset, price + offset) if action == "BUY" else (price + offset, price - offset)
        return SignalResponse(
            strategy=self.name, symbol=self.symbol, action=action,
            sl=round(sl, 2), tp=round(tp, 2), lot=self.lot,
            magic=self.magic, signal_id=sig_id, ts=ts,
        )

    def _last_price(self) -> float:
        try:
            df = self.data.recent_bars(self.symbol, 2)
            if not df.empty:
                return float(df["close"].iloc[-1])
        except Exception as e:                              # dummy must never hard-fail
            logger.warning(f"[{self.name}] dummy last-price fallback: {e}")
        return 10000.0


class ORBStrategy(BaseStrategy):
    """Opening Range Breakout, reusing the exact backtest logic in strategy_orb."""

    def evaluate(self) -> SignalResponse:
        now = pd.Timestamp.utcnow()
        today = now.normalize()
        date_tag = today.strftime("%Y%m%d")
        session = self.spec["session"]
        sess = self.cfg["symbols"][self.symbol]["sessions"][session]
        h, m = map(int, sess["open"].split(":"))

        # per-slot params override the global `orb` section (so backtest config is untouched)
        oc = self.cfg["orb"]
        p = self.spec.get("params", {})

        # DST-aware open: an equity-index cash open moves with US DST. The configured
        # open is the US-summer (DST) UTC time; under US standard time it is 1h later.
        if p.get("dst_open"):
            h, m = self._dst_adjust_open(today, h, m)
        open_str = f"{h:02d}:{m:02d}"

        range_minutes = p.get("range_minutes", oc["range_minutes"])
        use_sl = p.get("use_sl", True)
        range_filter = p.get("range_filter", False)         # skip abnormal-size opening ranges
        trend_sma = p.get("trend_sma")                      # only trade WITH the daily-SMA trend
        breakeven_r = p.get("breakeven_r")                  # once +Xr favorable, exit at entry on retrace (signal-driven BE)
        session_end = p.get("session_end_utc")              # e.g. "20:00" -> close by time
        params = ORBParams(
            range_minutes=range_minutes,
            tp_multiplier=p.get("tp_mult", oc["tp_multiplier"]),
            sl_multiplier=p.get("sl_mult", oc["sl_multiplier"]),
            entry_buffer=oc["entry_buffer_pips"] * self.cfg["symbols"][self.symbol]["pip_size"],
            max_trades_per_session=oc["max_trades_per_session"],
        )

        range_end = today.replace(hour=h, minute=m) + pd.Timedelta(minutes=range_minutes)
        if now < range_end:
            return self._flat(f"{date_tag}-{session}-PENDING", now.isoformat())

        # time exit: after session end, hold no position (the EA closes any open trade)
        if session_end:
            eh, em = map(int, session_end.split(":"))
            if now >= today.replace(hour=eh, minute=em):
                return self._flat(f"{date_tag}-{session}-CLOSED", now.isoformat())

        df = self.data.recent_bars(self.symbol, self.cfg["live"]["recent_bars"])
        df = df[df.index.normalize() == today]              # today only
        if df.empty:
            return self._flat(f"{date_tag}-{session}-NODATA", now.isoformat())

        # range-filter: only trade if today's opening range is 0.5-1.5x its 20-day median
        if range_filter:
            rs = today.replace(hour=h, minute=m)
            win = df[(df.index >= rs) & (df.index < range_end)]
            size = float(win["high"].max() - win["low"].min()) if len(win) else 0.0
            med = self._range_median(h, m, range_minutes, today)
            if med and size > 0 and not (0.5 * med <= size <= 1.5 * med):
                return self._flat(f"{date_tag}-{session}-FILTERED", now.isoformat())

        trades = generate_signals(df, self.symbol, session, open_str, params)
        if not trades:
            return self._flat(f"{date_tag}-{session}-NOBREAK", now.isoformat())

        t = trades[0]

        # trend filter: only take the breakout if it agrees with the daily-SMA trend
        # (skips counter-trend breakouts â€” the weaker side; FLAT on data error = fail-safe)
        if trend_sma:
            tdir = self._trend_dir(int(trend_sma), today)
            if tdir == 0 or (tdir > 0) != (t.direction == "long"):
                return self._flat(f"{date_tag}-{session}-TRENDFILTER", now.isoformat())

        # live outcome: once price has touched SL/TP the trade is OVER (matches the
        # backtest, which exits there). Without this the slot keeps emitting BUY/SELL
        # all session â€” and if price has whipsawed past the SL, the EA spams the broker
        # with an already-underwater stop ("invalid stops", err 10016).
        done = self._exit_hit(df, t, use_sl, breakeven_r)
        if done:
            return self._flat(f"{date_tag}-{session}-{t.direction.upper()}-{done}", now.isoformat())

        action = "BUY" if t.direction == "long" else "SELL"
        sl = round(t.sl_price, 5) if use_sl else 0.0        # 0.0 => EA sends no stop-loss
        sig_id = f"{self.symbol}-{self.name}-{date_tag}-{session}-{t.direction.upper()}"
        logger.info(f"[{self.name}] ORB {action} entry={t.entry_price} sl={sl} tp={t.tp_price}")
        return SignalResponse(
            strategy=self.name, symbol=self.symbol, action=action,
            sl=sl, tp=round(t.tp_price, 5), lot=self.lot,
            magic=self.magic, signal_id=sig_id, ts=now.isoformat(),
        )

    def _exit_hit(self, df, t, use_sl, breakeven_r=None) -> str | None:
        """Has the live price touched the trade's SL/TP since entry? Returns the exit
        reason ("SL"/"TP"/"BE") if the trade is over, else None â€” so the slot can go
        FLAT instead of chasing a finished (possibly stopped-out) trade.

        breakeven_r (optional): once price has run >= breakeven_r * risk in favour, the
        stop moves to ENTRY (0R). A retrace back to entry then exits at breakeven ("BE")
        â€” a signal-driven exit (the slot emits FLAT; the EA closes). Validated to lift
        the NAS 1:1 edge (OOS PF 1.33 -> 1.52). SL is checked before TP (pessimistic)."""
        post = df[df.index >= t.entry_ts]
        if post.empty:
            return None
        risk = abs(t.entry_price - t.sl_price)
        armed = False
        for _, bar in post.iterrows():
            if t.direction == "long":
                if breakeven_r is not None and not armed and (bar["high"] - t.entry_price) >= breakeven_r * risk:
                    armed = True
                if armed and bar["low"] <= t.entry_price:
                    return "BE"
                if use_sl and bar["low"] <= t.sl_price:
                    return "SL"
                if bar["high"] >= t.tp_price:
                    return "TP"
            else:  # short
                if breakeven_r is not None and not armed and (t.entry_price - bar["low"]) >= breakeven_r * risk:
                    armed = True
                if armed and bar["high"] >= t.entry_price:
                    return "BE"
                if use_sl and bar["high"] >= t.sl_price:
                    return "SL"
                if bar["low"] <= t.tp_price:
                    return "TP"
        return None

    def _range_median(self, h, m, range_minutes, today):
        """Median opening-range size over the last 20 sessions (cached once/day)."""
        cache = getattr(self, "_med_cache", {})
        if today in cache:
            return cache[today]
        big = self.data.recent_bars(self.symbol, 35000)     # ~25 days of M1; pulled once/day
        sizes = {}
        for date, day in big.groupby(big.index.date):
            st = pd.Timestamp(str(date), tz="UTC").replace(hour=h, minute=m)
            w = day[(day.index >= st) & (day.index < st + pd.Timedelta(minutes=range_minutes))]
            if len(w) >= range_minutes // 2:
                sz = float(w["high"].max() - w["low"].min())
                if sz > 0:
                    sizes[pd.Timestamp(str(date), tz="UTC")] = sz
        s = pd.Series(sizes).sort_index()
        prior = s[s.index < today]                          # exclude today
        med = float(prior.tail(20).median()) if len(prior) >= 10 else None
        cache[today] = med
        self._med_cache = cache
        logger.info(f"[{self.name}] range median(20d) = {med}")
        return med

    @staticmethod
    def _dst_adjust_open(today, h, m):
        """The configured open is the US-DST (summer) UTC open. When US Eastern is on
        standard time (winter), the equity cash open is one hour later in UTC."""
        import datetime
        from zoneinfo import ZoneInfo
        et = datetime.datetime(int(today.year), int(today.month), int(today.day), 12,
                               tzinfo=ZoneInfo("America/New_York"))
        if et.dst() == datetime.timedelta(0):           # standard time -> open 1h later
            total = h * 60 + m + 60
            return total // 60, total % 60
        return h, m

    def _trend_dir(self, n, today):
        """+1/-1/0 = sign of (last completed daily close - SMA(n) of daily closes).
        Only trade WITH this. Daily bars pulled straight from MT5 (n+5 bars), cached
        once/day. 0 (and any error) -> the caller goes FLAT (fail-safe)."""
        cache = getattr(self, "_trend_cache", {})
        key = (today, n)
        if key in cache:
            return cache[key]
        direction = 0
        try:
            import MetaTrader5 as mt5
            mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]
            rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_D1, 0, n + 5)
            if rates is not None and len(rates) > n:
                closes = pd.Series(rates["close"], dtype=float)
                closes = closes.iloc[:-1]                # drop today's still-forming daily bar
                if len(closes) >= n:
                    sma = float(closes.tail(n).mean())
                    last = float(closes.iloc[-1])
                    direction = 1 if last > sma else (-1 if last < sma else 0)
        except Exception as e:
            logger.warning(f"[{self.name}] trend_dir unavailable: {e}")
            direction = 0
        cache[key] = direction
        self._trend_cache = cache
        logger.info(f"[{self.name}] daily trend(SMA{n}) dir = {direction}")
        return direction


class VisionStrategy(BaseStrategy):
    """AI-vision slot: screenshot a chart, ask Claude (SMC), emit a SignalResponse.

    Same `() -> SignalResponse` contract as ORB, so SignalEngine treats it
    identically. Cadence-gated (the Claude call runs once per `interval_minutes`;
    every other poll serves the cached decision, preserving signal_id and thus EA
    idempotency). FAIL-SAFE: evaluate() never raises â€” any error degrades to the
    cached decision or a safe FLAT, so vision can never 500 the server or break
    the ORB slots.
    """

    def __init__(self, spec: dict, cfg: dict, data: DataProvider):
        super().__init__(spec, cfg, data)
        p = spec.get("params", {})
        self.interval = float(p.get("interval_minutes", 15))
        self.min_conf = int(p.get("min_confidence", 60))
        self.min_rr = float(p.get("min_rr", 1.5))
        # Reversing an OPEN position is a fresh entry against an existing trade, so
        # it must clear a (>=) higher confidence bar than a plain open â€” hysteresis
        # against flip-flopping on noise. Defaults to the entry bar (no extra gate).
        self.min_reverse_conf = int(p.get("min_reverse_confidence", self.min_conf))
        # Rule-based lock-profit reversal (NO Claude, runs every poll): once an open
        # position is in profit >= lock_min_profit_r, close it the moment price breaks
        # the swing of the last `reversal_lookback` completed `reversal_tf` bars
        # against the trade â€” banks profit fast without burning tokens. Entry stays
        # Claude's job; this is a cheap exit guard only.
        self.lock_profit = bool(p.get("lock_profit_reversal", False))
        self.lock_min_profit_r = float(p.get("lock_min_profit_r", 0.5))
        self.reversal_tf = str(p.get("reversal_tf", "M5"))
        self.reversal_lookback = int(p.get("reversal_lookback", 3))
        self.archive_all = bool(p.get("archive_all_frames", False))
        self.active_windows = self._parse_windows(p.get("active_windows_utc", []))
        tfs = p.get("timeframes")
        if isinstance(tfs, str):
            tfs = [t.strip() for t in tfs.split(",") if t.strip()]
        self.timeframes = list(tfs) if tfs else []   # multi-TF SMC when set
        self.capturer = ChartCapturer(spec, cfg)
        self.analyzer = VisionAnalyzer(spec, cfg)
        self.state = SlotState(self.symbol, self.name)
        self.journal = VisionJournal(cfg)

    def evaluate(self) -> SignalResponse:
        now_ts = pd.Timestamp.utcnow()
        now = now_ts.isoformat()
        try:
            # 0. lock-profit reversal â€” rule-based, NO Claude, runs every poll. Only
            #    acts on an in-profit open position; closes it on a structure flip so
            #    gains are banked before price retraces. Works off-hours/between
            #    Claude cycles, costs zero tokens.
            locked = self._lock_profit_check(now)
            if locked is not None:
                return locked

            # 1. active-hours gate â€” outside the configured trading windows we
            #    never call Claude (zero tokens). Serve the cached decision so an
            #    already-open position is left for the broker SL/TP to manage.
            if not self._within_active_hours(now_ts):
                return self.state.cached() or self._flat("OFFHOURS", now)

            # 1. cadence gate â€” between intervals, serve the cached decision so
            #    signal_id is stable and the EA does nothing.
            if not self.state.due(self.interval):
                return self.state.cached() or self._flat("BOOT", now)

            # 2. capture -> analyze (capture can raise; analyze never does)
            prev = self.state.prev_action
            bars = self.state.bars_in_state
            try:
                if self.timeframes:
                    images = self.capturer.capture_multi(self.symbol)
                    broker_px = self._entry_price()          # right after screenshot -> offset aligned
                    decision = self.analyzer.analyze_multi(images, self.symbol, prev, bars,
                                                           broker_price=broker_px)
                    png = images[-1][1] if images else b""   # lowest TF frame for the journal
                else:
                    png = self.capturer.capture(self.symbol)
                    broker_px = self._entry_price()
                    decision = self.analyzer.analyze(png, self.symbol, prev, bars,
                                                     broker_price=broker_px)
            except Exception:
                logger.exception(f"[{self.name}] vision capture/analyze error")
                return self.state.cached() or self._flat("ERROR", now)

            # 3. ENTRY/EXIT split (best practice: guards gate ENTRIES only). An
            #    already-open position is managed by the SL/TP set at entry and is
            #    closed ONLY on an explicit Claude FLAT or a guard-clearing,
            #    high-confidence reversal â€” never force-closed by re-checking RR
            #    against the moving price, and its SL/TP are never widened mid-trade.
            prev = self.state.prev_action               # the position the slot holds now
            raw = decision.get("action", "FLAT")
            if prev == "FLAT":
                action = self._apply_guards(decision)            # open only if it clears the bar
            elif raw == prev:
                action = prev                                    # same direction -> HOLD
            elif raw == "FLAT":
                action = "FLAT"                                  # Claude explicitly exits
            else:                                                # opposite -> reverse only if convincing
                conf = int(decision.get("confidence", 0) or 0)
                reverse_ok = self._apply_guards(decision) == raw and conf >= self.min_reverse_conf
                action = raw if reverse_ok else prev             # else keep the open trade
            is_hold = action != "FLAT" and action == prev        # keeping an existing position

            # 4. commit (signal_id lifecycle) + journal, then cache & return
            def builder(sig_id: str) -> SignalResponse:
                if action == "FLAT":
                    return flat(self.name, self.symbol, self.magic, sig_id, now)
                if is_hold and self.state.cached() is not None:
                    sl, tp = self.state.cached().sl, self.state.cached().tp  # keep entry SL/TP â€” never widen
                else:
                    sl, tp = round(float(decision["sl"]), 5), round(float(decision["tp"]), 5)
                return SignalResponse(
                    strategy=self.name, symbol=self.symbol, action=action,
                    sl=sl, tp=tp, lot=self.lot, magic=self.magic, signal_id=sig_id, ts=now,
                )

            resp = self.state.commit(action, builder)
            self.journal.record(self.symbol, self.name, png, decision,
                                resp.signal_id, self.state.last_changed, self.archive_all)
            return resp
        except Exception:                       # absolute backstop â€” never propagate
            logger.exception(f"[{self.name}] vision evaluate fatal")
            return self.state.cached() or self._flat("ERROR", now)

    def _parse_windows(self, spec) -> list[tuple[int, int]]:
        """Parse 'HH:MM-HH:MM' UTC windows into (start_min, end_min) minute pairs.

        Accepts a list or a comma-separated string. Empty -> [] = always active.
        A window may wrap midnight UTC (start > end), e.g. '22:00-06:00'.
        """
        if isinstance(spec, str):
            spec = [w.strip() for w in spec.split(",") if w.strip()]
        out: list[tuple[int, int]] = []
        for w in spec or []:
            try:
                a, b = str(w).split("-")
                sh, sm = (int(x) for x in a.split(":"))
                eh, em = (int(x) for x in b.split(":"))
                out.append((sh * 60 + sm, eh * 60 + em))
            except Exception:
                logger.warning(f"[{self.name}] bad active_windows_utc entry {w!r}, ignored")
        return out

    def _within_active_hours(self, now_ts) -> bool:
        """True if `now_ts` (UTC) falls in any configured window (or none set)."""
        if not self.active_windows:
            return True
        m = now_ts.hour * 60 + now_ts.minute
        for start, end in self.active_windows:
            if start <= end:
                if start <= m < end:
                    return True
            elif m >= start or m < end:        # window wraps midnight
                return True
        return False

    def _apply_guards(self, d: dict) -> str:
        """Confidence < min_confidence or RR < min_rr -> FLAT."""
        action = d.get("action", "FLAT")
        if action == "FLAT":
            return "FLAT"
        if int(d.get("confidence", 0) or 0) < self.min_conf:
            logger.info(f"[{self.name}] guard: confidence {d.get('confidence')} < {self.min_conf} -> FLAT")
            return "FLAT"
        rr = self._rr(d)
        if rr is None or rr < self.min_rr:
            logger.info(f"[{self.name}] guard: RR {rr} < {self.min_rr} -> FLAT")
            return "FLAT"
        return action

    def _rr(self, d: dict) -> float | None:
        """Reward:risk using the latest price as the entry reference."""
        entry = self._entry_price()
        if entry is None:
            return None
        sl = float(d.get("sl", 0) or 0)
        tp = float(d.get("tp", 0) or 0)
        if sl <= 0 or tp <= 0:
            return None
        risk = abs(entry - sl)
        if risk <= 0:
            return None
        return abs(tp - entry) / risk

    def _entry_price(self) -> float | None:
        try:
            df = self.data.recent_bars(self.symbol, 1)
            if df is not None and not df.empty:
                return float(df["close"].iloc[-1])
        except Exception as e:
            logger.warning(f"[{self.name}] entry price unavailable: {e}")
        return None

    # ---------------------------------------------------- lock-profit reversal
    def _lock_profit_check(self, now: str) -> SignalResponse | None:
        """If holding an in-profit position and price flips structure against it,
        close to bank the profit. Rule-based (no Claude). Returns a FLAT response
        to commit, or None to leave the position alone. Never raises."""
        if not self.lock_profit or self.state.prev_action == "FLAT":
            return None
        try:
            pos = self._open_position()
            if pos is None:                      # not filled yet, or already closed â€” don't act
                return None
            if not self._reversal_hit(pos):
                return None
            logger.info(f"[{self.name}] lock-profit: structure flip vs "
                        f"{self.state.prev_action} -> close to bank profit")
            return self.state.commit(
                "FLAT", lambda sid: flat(self.name, self.symbol, self.magic, sid, now))
        except Exception:
            logger.exception(f"[{self.name}] lock-profit check error")
            return None

    def _open_position(self):
        """The live MT5 position for this slot's magic+symbol, or None."""
        import MetaTrader5 as mt5
        mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]
        for p in (mt5.positions_get(symbol=mt5_symbol) or ()):
            if p.magic == self.magic:
                return p
        return None

    def _reversal_hit(self, pos) -> bool:
        """True if the position is in profit >= lock_min_profit_r AND price has broken
        the swing of the last `reversal_lookback` completed `reversal_tf` bars against it."""
        import MetaTrader5 as mt5
        direction = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
        entry, sl, current = float(pos.price_open), float(pos.sl or 0), float(pos.price_current)

        profit = (current - entry) if direction == "BUY" else (entry - current)
        if profit <= 0:
            return False
        if sl > 0:                               # require >= min R of profit when an SL exists
            risk = abs(entry - sl)
            if risk > 0 and (profit / risk) < self.lock_min_profit_r:
                return False

        bars = self._reversal_bars()
        if bars is None or len(bars) < self.reversal_lookback:
            return False
        return self._is_reversal(direction, current, bars)

    def _reversal_bars(self):
        """Last `reversal_lookback` COMPLETED bars on `reversal_tf` (resampled from
        live M1), excluding the still-forming bar. None if unavailable."""
        rule = {"M1": "1min", "M5": "5min", "M15": "15min", "M30": "30min"}.get(
            self.reversal_tf, "5min")
        m1 = self.data.recent_bars(self.symbol, 600)
        if m1 is None or m1.empty:
            return None
        agg = m1.resample(rule).agg({"high": "max", "low": "min"}).dropna()
        if len(agg) < self.reversal_lookback + 1:
            return None
        return agg.iloc[-(self.reversal_lookback + 1):-1]      # drop the forming bar

    @staticmethod
    def _is_reversal(direction: str, current: float, bars) -> bool:
        """Pure rule: a SELL is reversed when price breaks ABOVE the recent swing high;
        a BUY when it breaks BELOW the recent swing low."""
        if direction == "SELL":
            return current > float(bars["high"].max())
        return current < float(bars["low"].min())


class ZRevStrategy(BaseStrategy):
    """Z Strategy â€” always-in Donchian stop-and-reverse (validated XAU champion:
    entry_n=100, exit_n=20, no filter). Same semantics as
    pipeline/backtest/strategy_zrev.simulate(): while flat, enter on a break of the
    entry channel (max/min of the last `entry_n` completed 1H bars); while in a
    position, exit on a break of the (tighter) exit channel (last `exit_n` bars),
    reversing only if the entry channel broke too.

    Decisions use COMPLETED 1H bars for the channel and the CURRENT forming hour's
    running high/low for the break, so entries fire near the channel level (matching
    the backtest fill). Idempotent via a per-slot counter -> `signal_id` changes only
    when the desired position changes. Exit is SIGNAL-driven (the server emits
    FLAT/reverse as the trailing exit channel moves) â€” exactly like the backtest,
    which has no fixed TP/SL. A protective broker SL is set at the exit-channel level
    as a server-downtime backstop only (set `use_sl: false` to send no stop).

    Restart-safe: on the first evaluate it reconciles `prev_action` from any existing
    MT5 position under this magic, so a server restart never force-closes a live leg.
    """

    def __init__(self, spec: dict, cfg: dict, data: DataProvider):
        super().__init__(spec, cfg, data)
        p = spec.get("params", {})
        self.entry_n = int(p.get("entry_n", 100))
        self.exit_n = int(p.get("exit_n", 20))
        self.timeframe = str(p.get("timeframe", "1h"))   # bar timeframe for channels (1h, 4h, ...)
        self.use_sl = bool(p.get("use_sl", True))
        # M1 bars pulled per poll; must cover > entry_n completed hours with margin.
        self.history_bars = int(p.get("history_bars", 30000))   # ~500 trading hours
        # Optional EMA trend filter (DD reducer): only enter WITH the trend; an
        # against-trend channel break EXITS to flat instead of reversing. Matches
        # strategy_zrev.simulate(trend_filter=...). Audited: EMA100 cuts XAU maxDD
        # ~19% and lifts PF. Off => pure always-in S&R (never flat).
        self.trend_filter = bool(p.get("trend_filter", False))
        self.trend_ema = int(p.get("trend_ema", 200))
        # Optional SECOND (higher-timeframe) gate: also require the DAILY SMA trend to
        # agree before entering (multi-timeframe alignment). Audited DD reducer: cuts
        # the counter-secular-trend trades that drive the drawdown (PF up, DD -23%).
        self.daily_filter = bool(p.get("daily_filter", False))
        self.daily_sma = int(p.get("daily_sma", 50))
        # Optional THIRD gate â€” trend STRENGTH (not direction): only enter when the previous
        # completed DAILY Wilder ADX(adx_period) >= adx_min, i.e. gold is genuinely trending.
        # The other two gates say WHICH WAY; this one says WHETHER TO PLAY AT ALL. Validated
        # (research/regime_fix.py + final_1000.py, path-dependent equity from $1000):
        #   gate 0 (old live) -> maxDD -62%, 5/6 green | gate 28 -> maxDD -19%, 6/6 green.
        # Cost is real: Z trades 549 -> 187 and Z only profits in trending years; ORB+Reversal
        # carry the chop. Chosen for LOWEST DD + only all-green setting, not for max profit.
        # 0 = off. Fail-safe: ADX unavailable -> 0.0 -> blocks new entries (same policy as
        # _daily_trend). Gates entries AND reversals, never forces an exit â€” matches the
        # backtest, where an against-gate channel break exits to FLAT instead of reversing.
        self.adx_min = float(p.get("adx_min", 0.0))
        self.adx_period = int(p.get("adx_period", 14))
        # Dynamic lot by entry MOMENTUM (z-score of price vs 20-bar mean, direction-
        # adjusted). Validated: strong-momentum breakouts are much better trades, so
        # size up on high z, min lot on weak. Linear lot_min..lot_max over z_lo..z_hi,
        # floored to the 0.01 step. Kelly-bounded; fail-safe -> lot on any error.
        self.dynamic_lot = bool(p.get("dynamic_lot", False))
        self.lot_min = float(p.get("lot_min", self.lot))
        self.lot_max = float(p.get("lot_max", self.lot))
        self.lot_z_lo = float(p.get("lot_z_lo", 1.0))
        self.lot_z_hi = float(p.get("lot_z_hi", 3.0))
        # Capital-aware lot cap: the effective lot_max = min(lot_max, balance *
        # lot_per_balance). So a small account never over-leverages -- e.g. 0.00005
        # gives ~0.02 max at $400, scaling up to the 0.03 ceiling at ~$600+. 0 = off.
        self.lot_per_balance = float(p.get("lot_per_balance", 0.0))
        # Optional TIGHTER broker-SL backstop: min(channel, entry -/+ mult*ATR). Audited
        # marginal gain (PF 2.19->2.23, DD/MC better) AND a closer per-trade stop than the
        # wide channel level. 0 = off (use channel level only). Channel break stays the
        # primary signal-driven exit; this only caps fast adverse moves.
        self.atr_stop_mult = float(p.get("atr_stop_mult", 0.0))
        self._prev_action = "FLAT"
        self._counter = 0
        self._reconciled = False

    def evaluate(self) -> SignalResponse:
        now = pd.Timestamp.utcnow()
        ts = now.isoformat()
        self._reconcile_position()

        df = self.data.recent_bars(self.symbol, self.history_bars)
        if df.empty:
            return self._emit("FLAT", 0.0, ts)               # no data -> hold flat
        h = self._resample(df)
        if len(h) < 2:
            return self._emit("FLAT", 0.0, ts)

        # split off the still-forming current bar; decide on completed bars
        cur_bar = now.floor(self.timeframe)
        if h.index[-1] == cur_bar and len(h) > 1:
            completed, forming = h.iloc[:-1], h.iloc[-1]
        else:
            completed, forming = h, h.iloc[-1]

        min_bars = self.entry_n + 1
        if self.trend_filter:
            min_bars = max(min_bars, self.trend_ema + 1)     # EMA needs its span to settle
        if len(completed) < min_bars:
            return self._emit("FLAT", 0.0, ts)               # warming up

        upper   = float(completed["high"].iloc[-self.entry_n:].max())
        lower   = float(completed["low"].iloc[-self.entry_n:].min())
        exit_up = float(completed["high"].iloc[-self.exit_n:].max())
        exit_dn = float(completed["low"].iloc[-self.exit_n:].min())
        hi, lo  = float(forming["high"]), float(forming["low"])

        # trend gate (from completed bars only -> no lookahead): with the filter on,
        # only long while close>EMA, only short while close<EMA.
        can_long = can_short = True
        if self.trend_filter:
            ema = completed["close"].ewm(span=self.trend_ema, adjust=False).mean()
            up_trend = float(completed["close"].iloc[-1]) > float(ema.iloc[-1])
            can_long, can_short = up_trend, not up_trend
        if self.daily_filter:                            # higher-TF confirmation
            dd = self._daily_trend(now)
            can_long = can_long and (dd == 1)
            can_short = can_short and (dd == -1)
        if self.adx_min > 0:                             # trend-STRENGTH gate: sit out the chop
            strong = self._daily_adx(now) >= self.adx_min
            can_long = can_long and strong
            can_short = can_short and strong

        prev = self._prev_action
        if prev == "BUY":                                    # currently long
            if lo <= exit_dn:                                # long exits on exit channel
                action = "SELL" if (lo <= lower and can_short) else "FLAT"  # reverse only if entry broke + trend allows
            else:
                action = "BUY"
        elif prev == "SELL":                                 # currently short
            if hi >= exit_up:
                action = "BUY" if (hi >= upper and can_long) else "FLAT"
            else:
                action = "SELL"
        else:                                                # currently flat
            if hi >= upper and can_long:
                action = "BUY"
            elif lo <= lower and can_short:
                action = "SELL"
            else:
                action = "FLAT"

        action = _governed(action, prev)                     # monthly governor: block new entry/reverse

        if action == "BUY":
            sl = exit_dn if self.use_sl else 0.0
        elif action == "SELL":
            sl = exit_up if self.use_sl else 0.0
        else:
            sl = 0.0
        if self.atr_stop_mult > 0 and self.use_sl and action in ("BUY", "SELL"):
            sl = self._atr_tighten(completed, forming, action, sl)
        lot = self._dynamic_lot(completed, forming, action) if action in ("BUY", "SELL") else self.lot
        if action in ("BUY", "SELL") and action != prev:                # new entry / reversal -> guards
            mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]
            if _book_conflict(mt5_symbol, action, self.spec.get("magic")):
                logger.info(f"[{self.name}] net-exposure: book already {action} {self.symbol} -> SKIP entry")
                return self._emit("FLAT", 0.0, ts)
            if not _risk_ok(float(forming["close"]), sl, lot, mt5_symbol):
                logger.info(f"[{self.name}] risk cap: {action} stop too wide (> ${_risk_cap():.0f}) -> SKIP entry")
                return self._emit("FLAT", 0.0, ts)
        return self._emit(action, sl, ts, lot)

    def _balance(self) -> float | None:
        """Account balance from MT5 (for capital-aware lot sizing). None on error."""
        try:
            import MetaTrader5 as mt5
            ai = mt5.account_info()
            return float(ai.balance) if ai else None
        except Exception:
            return None

    def _atr_tighten(self, completed, forming, action: str, sl: float) -> float:
        """Tighten the broker-SL backstop to the closer of the channel level and
        entry -/+ atr_stop_mult * ATR(14). Fail-safe -> original channel sl on error."""
        try:
            h = completed
            tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                            (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
            atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])
            price = float(forming["close"])
            if atr <= 0:
                return sl
            if action == "BUY":
                return max(sl, price - self.atr_stop_mult * atr)   # higher = tighter for a long
            return min(sl, price + self.atr_stop_mult * atr)       # lower = tighter for a short
        except Exception as e:
            logger.warning(f"[{self.name}] atr_tighten fallback: {e}")
            return sl

    def _dynamic_lot(self, completed, forming, action: str) -> float:
        """Lot from entry momentum: z = (price - 20-bar mean)/std, signed so a strong
        breakout in the trade direction -> bigger lot. Linear lot_min..lot_max over
        z_lo..z_hi, floored to 0.01. Fail-safe -> base lot on any error."""
        if not self.dynamic_lot:
            return self.lot
        try:
            win = completed["close"].iloc[-20:]
            ma, sd = float(win.mean()), float(win.std())
            if sd <= 0:
                return self.lot_min
            z = (float(forming["close"]) - ma) / sd
            z_dir = z if action == "BUY" else -z
            eff_max = self.lot_max
            if self.lot_per_balance > 0:                 # capital-aware cap
                bal = self._balance()
                if bal:
                    eff_max = max(self.lot_min, min(self.lot_max, bal * self.lot_per_balance))
            frac = max(0.0, min(1.0, (z_dir - self.lot_z_lo) / max(self.lot_z_hi - self.lot_z_lo, 1e-9)))
            raw = self.lot_min + frac * (eff_max - self.lot_min)
            lot = max(self.lot_min, min(eff_max, int(round(raw / 0.01 - 1e-9)) * 0.01))
            logger.info(f"[{self.name}] dynamic lot: z_dir={z_dir:.2f} cap={eff_max:.3f} -> lot={lot:.2f}")
            return round(lot, 2)
        except Exception as e:
            logger.warning(f"[{self.name}] dynamic_lot fallback: {e}")
            return self.lot

    def _emit(self, action: str, sl: float, ts: str, lot: float | None = None) -> SignalResponse:
        if action != self._prev_action:                      # signal_id lifecycle
            self._counter += 1
            self._prev_action = action
        sig_id = f"{self.symbol}-{self.name}-ZREV-{self._counter}"
        if action == "FLAT":
            return flat(self.name, self.symbol, self.magic, sig_id, ts)
        use_lot = self.lot if lot is None else lot
        logger.info(f"[{self.name}] ZREV {action} sl={round(sl, 5)} lot={use_lot}")
        return SignalResponse(
            strategy=self.name, symbol=self.symbol, action=action,
            sl=round(sl, 5), tp=0.0, lot=use_lot,
            magic=self.magic, signal_id=sig_id, ts=ts,
        )

    def _resample(self, df):
        """1m OHLCV -> self.timeframe bars (1h, 4h, ...), dropping empty hours."""
        return (df.resample(self.timeframe)
                  .agg({"open": "first", "high": "max", "low": "min",
                        "close": "last", "volume": "sum"})
                  .dropna(subset=["open"]))

    def _daily_trend(self, now) -> int:
        """+1/-1/0 = sign of (last completed daily close - SMA(daily_sma)). Daily bars
        pulled from MT5 (D1), cached once/day. 0 (and any error) -> blocks NEW entries
        (fail-safe). Mirrors the backtest's daily-trend gate."""
        today = now.normalize()
        cache = getattr(self, "_dtrend_cache", {})
        key = (today, self.daily_sma)
        if key in cache:
            return cache[key]
        direction = 0
        try:
            import MetaTrader5 as mt5
            mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]
            rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_D1, 0, self.daily_sma + 5)
            if rates is not None and len(rates) > self.daily_sma:
                closes = pd.Series(rates["close"], dtype=float).iloc[:-1]   # drop forming day
                if len(closes) >= self.daily_sma:
                    sma = float(closes.tail(self.daily_sma).mean())
                    last = float(closes.iloc[-1])
                    direction = 1 if last > sma else (-1 if last < sma else 0)
        except Exception as e:
            logger.warning(f"[{self.name}] daily_trend unavailable: {e}")
            direction = 0
        cache[key] = direction
        self._dtrend_cache = cache
        logger.info(f"[{self.name}] daily trend(SMA{self.daily_sma}) dir = {direction}")
        return direction

    def _daily_adx(self, now) -> float:
        """Wilder ADX(adx_period) on COMPLETED daily bars â€” the forming day is dropped, which is
        the live equivalent of the backtest's .shift(1) (research/regime_fix.py: adx_daily). Daily
        bars from MT5 (D1), cached once/day. 0.0 (and any error) -> blocks NEW entries whenever
        adx_min > 0 (fail-safe, same policy as _daily_trend)."""
        today = now.normalize()
        cache = getattr(self, "_dadx_cache", {})
        key = (today, self.adx_period)
        if key in cache:
            return cache[key]
        value = 0.0
        try:
            import MetaTrader5 as mt5
            import numpy as np
            n = self.adx_period
            mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]
            rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_D1, 0, max(n * 10, 150))
            if rates is not None and len(rates) > n * 3:
                d = pd.DataFrame(rates)[["high", "low", "close"]].astype(float).iloc[:-1]  # drop forming day
                h, l, c = d["high"], d["low"], d["close"]
                up, dn = h.diff(), -l.diff()
                plus = np.where((up > dn) & (up > 0), up, 0.0)
                minus = np.where((dn > up) & (dn > 0), dn, 0.0)
                tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
                atr = tr.ewm(alpha=1 / n, adjust=False).mean()
                pdi = 100 * pd.Series(plus, index=d.index).ewm(alpha=1 / n, adjust=False).mean() / atr
                mdi = 100 * pd.Series(minus, index=d.index).ewm(alpha=1 / n, adjust=False).mean() / atr
                dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
                last = float(dx.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])
                value = last if last == last else 0.0                          # NaN -> fail-safe 0
        except Exception as e:
            logger.warning(f"[{self.name}] daily_adx unavailable: {e}")
            value = 0.0
        cache[key] = value
        self._dadx_cache = cache
        logger.info(f"[{self.name}] daily ADX({self.adx_period}) = {value:.1f} "
                    f"(min {self.adx_min:.0f} -> {'TRADE' if value >= self.adx_min else 'SIT OUT'})")
        return value

    def _reconcile_position(self) -> None:
        """On first poll, adopt any existing MT5 position under this magic as the
        current state, so a server restart never emits FLAT and force-closes a leg."""
        if self._reconciled:
            return
        try:
            import MetaTrader5 as mt5
            mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]
            poss = mt5.positions_get(symbol=mt5_symbol)
            if poss is None:                  # MT5 not ready yet -> retry next poll
                return                        # (do NOT mark reconciled / emit FLAT and close a live leg)
            for p in poss:
                if p.magic == self.magic:
                    self._prev_action = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
                    logger.info(f"[{self.name}] reconciled to existing {self._prev_action}")
                    break
            self._reconciled = True           # only once MT5 actually responded
        except Exception as e:
            logger.warning(f"[{self.name}] reconcile retry (MT5 error): {e}")


class MeanReversionStrategy(BaseStrategy):
    """Mean-reversion (z-score fade) on H1 â€” validated XAU diversifier to Z
    (OOS PF 2.7-3.2, 11/11 walk-forward, survives heavy cost, M1-fill confirmed).

    When the latest COMPLETED H1 close sits >= entry_z standard deviations from the
    N-bar mean, fade it back toward the mean: TP = the mean, SL = stop_z std beyond
    the mean (a tight stop just past entry). Flat between signals; one entry per H1
    bar. Exit is broker-managed (TP/SL set at entry) so an open trade is safe even if
    the server dies; a brain-side time-exit emits FLAT after max_hold_hours if neither
    is hit. Idempotent via a per-slot counter. Restart-safe: first poll adopts any
    existing MT5 position under this magic; each poll detects a broker TP/SL closure
    and resets to flat."""

    def __init__(self, spec: dict, cfg: dict, data: DataProvider):
        super().__init__(spec, cfg, data)
        p = spec.get("params", {})
        self.N = int(p.get("lookback", 20))
        self.entry_z = float(p.get("entry_z", 2.5))
        self.stop_z = float(p.get("stop_z", 3.0))
        self.max_hold_h = int(p.get("max_hold_hours", 48))
        self.history_bars = int(p.get("history_bars", 6000))
        self._prev_action = "FLAT"
        self._counter = 0
        self._sl = 0.0
        self._tp = 0.0
        self._entry_ts = None
        self._last_bar_ts = None
        self._reconciled = False

    def evaluate(self) -> SignalResponse:
        now = pd.Timestamp.utcnow()
        ts = now.isoformat()
        self._reconcile()

        df = self.data.recent_bars(self.symbol, self.history_bars)
        if df.empty:
            return self._emit("FLAT", 0.0, 0.0, ts)
        h = resample_1h(df)
        cur_hour = now.floor("1h")
        completed = h.iloc[:-1] if (len(h) and h.index[-1] == cur_hour) else h
        cc = completed["close"]
        if len(cc) < self.N + 1:
            return self._emit("FLAT", 0.0, 0.0, ts)               # warming up

        # holding -> time-exit or hold (broker manages TP/SL)
        if self._prev_action in ("BUY", "SELL"):
            if self._entry_ts is not None and (now - self._entry_ts) >= pd.Timedelta(hours=self.max_hold_h):
                return self._emit("FLAT", 0.0, 0.0, ts)
            return self._emit(self._prev_action, self._sl, self._tp, ts)

        # flat -> look for a fresh z-score entry (one per completed H1 bar)
        last_bar = cc.index[-1]
        if self._last_bar_ts is not None and last_bar <= self._last_bar_ts:
            return self._emit("FLAT", 0.0, 0.0, ts)
        win = cc.iloc[-self.N - 1:-1]
        ma, sd = float(win.mean()), float(win.std())
        if sd <= 0:
            return self._emit("FLAT", 0.0, 0.0, ts)
        z = (float(cc.iloc[-1]) - ma) / sd
        if z <= -self.entry_z:
            self._entry_ts = now; self._last_bar_ts = last_bar
            return self._emit("BUY", round(ma - self.stop_z * sd, 5), round(ma, 5), ts)
        if z >= self.entry_z:
            self._entry_ts = now; self._last_bar_ts = last_bar
            return self._emit("SELL", round(ma + self.stop_z * sd, 5), round(ma, 5), ts)
        return self._emit("FLAT", 0.0, 0.0, ts)

    def _emit(self, action: str, sl: float, tp: float, ts: str) -> SignalResponse:
        if action != self._prev_action:
            self._counter += 1
            self._prev_action = action
        sig_id = f"{self.symbol}-{self.name}-MR-{self._counter}"
        if action == "FLAT":
            self._entry_ts = None; self._sl = self._tp = 0.0
            return flat(self.name, self.symbol, self.magic, sig_id, ts)
        self._sl, self._tp = sl, tp
        logger.info(f"[{self.name}] MR {action} sl={sl} tp={tp} lot={self.lot}")
        return SignalResponse(
            strategy=self.name, symbol=self.symbol, action=action,
            sl=sl, tp=tp, lot=self.lot, magic=self.magic, signal_id=sig_id, ts=ts,
        )

    def _reconcile(self) -> None:
        """First poll: adopt any existing MT5 position (restart-safe). Each poll: if
        we think we hold but MT5 has no position under this magic, the broker closed
        it (TP/SL) -> reset to flat. Never raises."""
        try:
            import MetaTrader5 as mt5
            mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]
            pos = None
            for p in (mt5.positions_get(symbol=mt5_symbol) or ()):
                if p.magic == self.magic:
                    pos = p; break
            if not self._reconciled:
                if pos is not None:
                    self._prev_action = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                    self._entry_ts = pd.Timestamp(pos.time, unit="s", tz="UTC")
                    self._sl, self._tp = pos.sl, pos.tp
                    logger.info(f"[{self.name}] reconciled to existing {self._prev_action}")
                self._reconciled = True
            elif self._prev_action in ("BUY", "SELL") and pos is None:
                logger.info(f"[{self.name}] position closed by broker (TP/SL) -> FLAT")
                self._prev_action = "FLAT"; self._entry_ts = None; self._sl = self._tp = 0.0
        except Exception as e:
            logger.warning(f"[{self.name}] reconcile skipped: {e}")


class LiquidityLimitStrategy(BaseStrategy):
    """Liquidity Limit Strategy â€” Supertrend band 'limit' entries (validated XAU candidate,
    research/supertrend_long_xau.py + supertrend_validate13.py, broker-matched $13/$26 stop).

    In an UPTREND it holds a BUY at the continuously-updated Supertrend SUPPORT band and enters
    when the forming bar pulls back to it (= the buy-limit fill); mirror SELL at the RESISTANCE
    band in a DOWNTREND. ONE position at a time: while a position is open, new entry signals are
    ignored (only the exit is watched). Fixed RR from the band level: TP = band +/- tp_dollars,
    SL = band -/+ sl_dollars. Exit is SIGNAL-DRIVEN (the slot emits FLAT when the forming bar
    touches TP or SL) with the same prices set as a broker backstop for server downtime.

    DESIRED-STATE: the EA has no pending-order type, so the 'limit' is realized as a MARKET entry
    the moment price reaches the band (same economic effect). Idempotent via a per-slot counter ->
    signal_id changes only when the desired position changes. Claude confirmation runs ASYNC via
    the shadow advisor (add this magic to advisor.watch); it annotates every entry, never blocks.
    Restart-safe: reconciles any existing MT5 position (and its broker SL/TP) on the first poll.
    """

    def __init__(self, spec: dict, cfg: dict, data: DataProvider):
        super().__init__(spec, cfg, data)
        p = spec.get("params", {})
        self.timeframe = str(p.get("timeframe", "1h"))
        self.st_period = int(p.get("st_period", 21))      # Wilder ATR period for Supertrend
        self.st_mult = float(p.get("st_mult", 5.5))       # band = src -/+ mult*ATR
        self.tp_dollars = float(p.get("tp_dollars", 26.0))  # TP distance in PRICE units ($ gold move)
        self.sl_dollars = float(p.get("sl_dollars", 13.0))  # SL distance ($13 @0.01 lot; broker-matched)
        self.both_sides = bool(p.get("both_sides", True))
        self.history_bars = int(p.get("history_bars", 15000))
        self._prev_action = "FLAT"
        self._counter = 0
        self._reconciled = False
        self._tp_lvl = 0.0
        self._sl_lvl = 0.0

    def evaluate(self) -> SignalResponse:
        now = pd.Timestamp.utcnow()
        ts = now.isoformat()
        self._reconcile_position()
        df = self.data.recent_bars(self.symbol, self.history_bars)
        if df.empty:
            return self._emit("FLAT", 0.0, 0.0, ts)
        h = self._resample(df)
        min_bars = self.st_period + 10
        if len(h) < min_bars + 1:
            return self._emit("FLAT", 0.0, 0.0, ts)              # warming up
        cur_bar = now.floor(self.timeframe)
        if h.index[-1] == cur_bar and len(h) > 1:
            completed, forming = h.iloc[:-1], h.iloc[-1]
        else:
            completed, forming = h, h.iloc[-1]
        if len(completed) < min_bars:
            return self._emit("FLAT", 0.0, 0.0, ts)

        up_band, dn_band, trend = self._supertrend(completed)
        hi, lo = float(forming["high"]), float(forming["low"])
        prev = self._prev_action

        if prev == "BUY":                                        # hold long -> exit on TP/SL touch
            hit = (self._sl_lvl > 0 and lo <= self._sl_lvl) or (self._tp_lvl > 0 and hi >= self._tp_lvl)
            action = "FLAT" if hit else "BUY"
        elif prev == "SELL":                                     # hold short
            hit = (self._sl_lvl > 0 and hi >= self._sl_lvl) or (self._tp_lvl > 0 and lo <= self._tp_lvl)
            action = "FLAT" if hit else "SELL"
        else:                                                    # flat -> band-limit fill
            if trend == 1 and lo <= up_band:
                action = "BUY"
            elif trend == -1 and self.both_sides and hi >= dn_band:
                action = "SELL"
            else:
                action = "FLAT"

        if action == "BUY" and prev != "BUY":                    # new entry -> lock fixed TP/SL
            self._sl_lvl = up_band - self.sl_dollars
            self._tp_lvl = up_band + self.tp_dollars
        elif action == "SELL" and prev != "SELL":
            self._sl_lvl = dn_band + self.sl_dollars
            self._tp_lvl = dn_band - self.tp_dollars

        sl = self._sl_lvl if action in ("BUY", "SELL") else 0.0
        tp = self._tp_lvl if action in ("BUY", "SELL") else 0.0
        return self._emit(action, sl, tp, ts)

    def _supertrend(self, h) -> tuple[float, float, int]:
        """(up_band, dn_band, trend) for the LAST completed bar. Ports the Wilder-ATR
        Supertrend in research/supertrend_long_xau.supertrend (band lock + flip)."""
        import numpy as np
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
        return float(up[-1]), float(dn[-1]), int(trend[-1])

    def _emit(self, action: str, sl: float, tp: float, ts: str) -> SignalResponse:
        if action != self._prev_action:
            self._counter += 1
            self._prev_action = action
        sig_id = f"{self.symbol}-{self.name}-LIQ-{self._counter}"
        if action == "FLAT":
            return flat(self.name, self.symbol, self.magic, sig_id, ts)
        logger.info(f"[{self.name}] LIQ {action} sl={round(sl, 2)} tp={round(tp, 2)} lot={self.lot}")
        return SignalResponse(
            strategy=self.name, symbol=self.symbol, action=action,
            sl=round(sl, 5), tp=round(tp, 5), lot=self.lot,
            magic=self.magic, signal_id=sig_id, ts=ts,
        )

    def _resample(self, df):
        return (df.resample(self.timeframe)
                  .agg({"open": "first", "high": "max", "low": "min",
                        "close": "last", "volume": "sum"})
                  .dropna(subset=["open"]))

    def _reconcile_position(self) -> None:
        """Adopt any existing MT5 position (and its broker SL/TP) under this magic on the
        first poll, so a restart never force-closes a live leg or loses the exit levels."""
        if self._reconciled:
            return
        try:
            import MetaTrader5 as mt5
            mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]
            poss = mt5.positions_get(symbol=mt5_symbol)
            if poss is None:                  # MT5 not ready -> retry next poll
                return
            for p in poss:
                if p.magic == self.magic:
                    self._prev_action = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
                    self._sl_lvl = float(p.sl); self._tp_lvl = float(p.tp)
                    logger.info(f"[{self.name}] reconciled to {self._prev_action} sl={p.sl} tp={p.tp}")
                    break
            self._reconciled = True
        except Exception as e:
            logger.warning(f"[{self.name}] reconcile retry: {e}")


class GoldenStrategy(BaseStrategy):
    """Golden Strategy 1 â€” the rehabilitated Semi-Martingale EA SIGNAL (martingale stripped),
    validated XAU M5. Fades a normalized-MACD(5/13/9)+normalized-price extreme (both <= level_low
    -> BUY / both >= level_high -> SELL over `norm` bars) ONLY with the H1-EMA(ema_trend) trend, and
    STANDS ASIDE in over-extended trends (H1 ADX(adx_period) > adx_max). Market entry; broker-managed
    ATR stop (atr_mult x ATR) + tp_r-R take-profit; one position; broker SL/TP is the exit.

    Backtest (research/semi_marti_*.py + regime_adaptive.py): the raw EA signal LOSES (fade blindly
    counter-trend); fading only WITH the fast H1 trend + skip ADX>40 -> PF 2.14, OOS-PF 1.99, maxDD
    -35% vs unfiltered, 11/11 walk-forward windows, 6/6 green years. Monthly-PnL corr to Z only +0.15
    (genuine XAU complement, unlike the retired LIQ). Restart-safe: adopts any existing MT5 position
    (and its broker SL/TP) on the first poll; each poll detects a broker close -> flat. One entry per
    completed M5 bar. DEMO paper-test slot (magic 920626); Claude annotates via the shadow advisor."""

    def __init__(self, spec: dict, cfg: dict, data: DataProvider):
        super().__init__(spec, cfg, data)
        p = spec.get("params", {})
        self.timeframe = str(p.get("timeframe", "5min"))
        self.trend_tf = str(p.get("trend_tf", "1h"))
        self.norm = int(p.get("norm_period", 100))
        self.lo = float(p.get("level_low", 15.0))
        self.hi = float(p.get("level_high", 80.0))
        self.macd_fast = int(p.get("macd_fast", 5))
        self.macd_slow = int(p.get("macd_slow", 13))
        self.macd_signal = int(p.get("macd_signal", 9))
        self.ema_trend = int(p.get("ema_trend", 15))       # H1 EMA whose slope defines the trend
        self.adx_period = int(p.get("adx_period", 14))
        self.adx_max = float(p.get("adx_max", 40.0))       # skip entries when trend over-extended
        self.atr_period = int(p.get("atr_period", 14))
        self.atr_mult = float(p.get("atr_mult", 3.0))      # SL = atr_mult x ATR(M5)
        self.tp_r = float(p.get("tp_r", 3.0))              # TP = tp_r x risk
        # REGIME-BASED SIZING (validated playbook rule, research/regime_sizing.py): size up in the
        # favorable low-ADX regime (Golden's best bin), stay base otherwise. 0.02 kept Sharpe while
        # lifting net +40% / OOS 1.99->2.22; 0.03 lifted return but HURT Sharpe (rejected).
        self.regime_sizing = bool(p.get("regime_sizing", False))
        self.size_adx_thresh = float(p.get("size_adx_thresh", 20.0))
        self.lot_favorable = float(p.get("lot_favorable", 0.02))
        self.history_bars = int(p.get("history_bars", 15000))
        self._prev_action = "FLAT"
        self._counter = 0
        self._sl = 0.0
        self._tp = 0.0
        self._lot = float(self.lot)                        # lot for the current (held) position
        self._last_bar_ts = None
        self._reconciled = False

    def evaluate(self) -> SignalResponse:
        now = pd.Timestamp.utcnow()
        ts = now.isoformat()
        self._reconcile()
        df = self.data.recent_bars(self.symbol, self.history_bars)
        if df.empty:
            return self._emit("FLAT", 0.0, 0.0, ts)
        m = self._resample(df, self.timeframe)
        h = self._resample(df, self.trend_tf)
        if len(m) < self.norm + 6 or len(h) < max(self.ema_trend, self.adx_period) + 3:
            return self._emit("FLAT", 0.0, 0.0, ts)               # warming up
        cur_m = now.floor(self.timeframe)
        m_c = m.iloc[:-1] if (m.index[-1] == cur_m and len(m) > 1) else m
        cur_h = now.floor(self.trend_tf)
        h_c = h.iloc[:-1] if (h.index[-1] == cur_h and len(h) > 1) else h

        if self._prev_action in ("BUY", "SELL"):                  # holding -> broker manages TP/SL
            return self._emit(self._prev_action, self._sl, self._tp, ts)

        last_bar = m_c.index[-1]                                   # one entry per completed M5 bar
        if self._last_bar_ts is not None and last_bar <= self._last_bar_ts:
            return self._emit("FLAT", 0.0, 0.0, ts)

        mnorm, pnorm = self._norm_indicators(m_c)
        trend = self._trend_dir(h_c)
        adx = self._adx(h_c)
        atr = self._atr(m_c)
        import numpy as np
        if not (np.isfinite(mnorm) and np.isfinite(pnorm) and np.isfinite(adx) and np.isfinite(atr)) or atr <= 0:
            return self._emit("FLAT", 0.0, 0.0, ts)
        if adx > self.adx_max:                                    # over-extended trend -> stand aside
            return self._emit("FLAT", 0.0, 0.0, ts)

        action = "FLAT"
        if mnorm <= self.lo and pnorm <= self.lo and trend > 0:   # buy the dip WITH the uptrend
            action = "BUY"
        elif mnorm >= self.hi and pnorm >= self.hi and trend < 0:  # sell the bounce WITH the downtrend
            action = "SELL"
        action = _governed(action, "FLAT")                        # monthly governor: block new entry
        if action == "FLAT":
            return self._emit("FLAT", 0.0, 0.0, ts)

        self._last_bar_ts = last_bar
        price = float(m_c["close"].iloc[-1])
        risk = self.atr_mult * atr
        if action == "BUY":
            sl, tp = price - risk, price + self.tp_r * risk
        else:
            sl, tp = price + risk, price - self.tp_r * risk
        # regime-based lot: size up in the favorable low-ADX regime (else base lot)
        self._lot = self.lot_favorable if (self.regime_sizing and adx < self.size_adx_thresh) else self.lot
        mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]     # net-exposure + per-trade risk cap
        if _book_conflict(mt5_symbol, action, self.spec.get("magic")):
            logger.info(f"[{self.name}] net-exposure: book already {action} {self.symbol} -> SKIP entry")
            return self._emit("FLAT", 0.0, 0.0, ts)
        if not _risk_ok(price, sl, self._lot, mt5_symbol):
            logger.info(f"[{self.name}] risk cap: {action} risk > ${_risk_cap():.0f} -> SKIP entry")
            return self._emit("FLAT", 0.0, 0.0, ts)
        return self._emit(action, round(sl, 5), round(tp, 5), ts)

    # ---- indicators (match research/semi_marti_*.py exactly) ----
    def _resample(self, df, tf):
        return (df.resample(tf).agg({"open": "first", "high": "max", "low": "min",
                                     "close": "last", "volume": "sum"}).dropna(subset=["open"]))

    def _norm_indicators(self, m):
        import numpy as np
        c = m["close"]
        macd_sig = (c.ewm(span=self.macd_fast, adjust=False).mean()
                    - c.ewm(span=self.macd_slow, adjust=False).mean()).rolling(self.macd_signal).mean()
        mn, mx = macd_sig.rolling(self.norm).min(), macd_sig.rolling(self.norm).max()
        mnorm = ((macd_sig - mn) / (mx - mn).replace(0, np.nan) * 100).iloc[-1]
        pmn, pmx = c.rolling(self.norm).min(), c.rolling(self.norm).max()
        pnorm = ((c - pmn) / (pmx - pmn).replace(0, np.nan) * 100).iloc[-1]
        return float(mnorm), float(pnorm)

    def _trend_dir(self, h):
        import numpy as np
        d = h["close"].ewm(span=self.ema_trend, adjust=False).mean().diff().iloc[-1]
        return int(np.sign(d)) if np.isfinite(d) else 0

    def _adx(self, h):
        import numpy as np
        n = self.adx_period
        up = h["high"].diff(); dn = -h["low"].diff()
        plus = np.where((up > dn) & (up > 0), up, 0.0)
        minus = np.where((dn > up) & (dn > 0), dn, 0.0)
        tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                        (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1 / n, adjust=False).mean()
        pdi = 100 * pd.Series(plus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
        mdi = 100 * pd.Series(minus, index=h.index).ewm(alpha=1 / n, adjust=False).mean() / atr
        dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
        return float(dx.ewm(alpha=1 / n, adjust=False).mean().iloc[-1])

    def _atr(self, m):
        tr = pd.concat([m["high"] - m["low"], (m["high"] - m["close"].shift()).abs(),
                        (m["low"] - m["close"].shift()).abs()], axis=1).max(axis=1)
        return float(tr.ewm(alpha=1 / self.atr_period, adjust=False).mean().iloc[-1])

    def _emit(self, action: str, sl: float, tp: float, ts: str) -> SignalResponse:
        if action != self._prev_action:
            self._counter += 1
            self._prev_action = action
        sig_id = f"{self.symbol}-{self.name}-GOLD-{self._counter}"
        if action == "FLAT":
            self._sl = self._tp = 0.0; self._lot = float(self.lot)
            return flat(self.name, self.symbol, self.magic, sig_id, ts)
        self._sl, self._tp = sl, tp
        logger.info(f"[{self.name}] GOLDEN {action} sl={sl} tp={tp} lot={self._lot}")
        return SignalResponse(
            strategy=self.name, symbol=self.symbol, action=action,
            sl=sl, tp=tp, lot=self._lot, magic=self.magic, signal_id=sig_id, ts=ts,
        )

    def _reconcile(self) -> None:
        """First poll: adopt any existing MT5 position (restart-safe). Each poll: if we think we
        hold but MT5 has none under this magic, the broker closed it (SL/TP) -> reset flat. Never raises."""
        try:
            import MetaTrader5 as mt5
            mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]
            pos = None
            for p in (mt5.positions_get(symbol=mt5_symbol) or ()):
                if p.magic == self.magic:
                    pos = p; break
            if not self._reconciled:
                if pos is not None:
                    self._prev_action = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
                    self._sl, self._tp = float(pos.sl), float(pos.tp); self._lot = float(pos.volume)
                    logger.info(f"[{self.name}] reconciled to existing {self._prev_action} sl={pos.sl} tp={pos.tp}")
                self._reconciled = True
            elif self._prev_action in ("BUY", "SELL") and pos is None:
                logger.info(f"[{self.name}] position closed by broker (SL/TP) -> FLAT")
                self._prev_action = "FLAT"; self._sl = self._tp = 0.0
        except Exception as e:
            logger.warning(f"[{self.name}] reconcile skipped: {e}")


# register new model types here; config `type:` selects one
class EternaStrategy(BaseStrategy):
    """Eterna â€” dual-Supertrend trend-follower on XAU H1. ONE parameter set, no ensemble.

    Derived from the third-party 'EA EternaBot V.2' (dual Supertrend), deployed with nearly
    every EA default INVERTED, because research/eterna_*.py (20 phases, ~1900 configs on 5.5y
    of XAUUSD 1m) showed the defaults lose money:
      - martingale OFF     (the EA's apparent accuracy WAS the martingale illusion â€” same
                            finding as research/semi_marti_signal.py, commit 62ea652)
      - H1, not M1/M5      (EA recommends scalping; M5 lost -$21.7k, 97% red months)
      - CONSERVATIVE gate  (enter only WITH the trend Supertrend; won 5/5 walk-forward windows
                            while the numeric params changed 4/5 -> the edge is STRUCTURAL)
      - no trailing / no breakeven / no partial TP / no SL buffer / no hour filter
        (every structural variant tested made Ret/DD worse)

    Signal: entry Supertrend(atr_period, mult_entry) FLIPS on a CLOSED bar -> candidate
    direction; taken only when Supertrend(atr_period, mult_trend) agrees. SL = extreme of the
    last `struct_bars` CLOSED bars â€” the EA ties this lookback to ATR_Period (EA line 259:
    `int bars = (int)ATR_Period;`), so struct_bars defaults to atr_period. TP = tp_ratio x that
    risk. Broker SL/TP is the exit; an opposite flip closes early.

    Validated @ $1000, 0.01 lot, $0.50/trade (research/eterna_revalidate.py):
      full 5.5y : net $2790, PF 1.57, maxDD -14.1%, 51%/yr, Ret/DD 3.63, 6/6 green years
      2026 YTD  : net +$2159 (+216%), PF 2.69, maxDD -12.6%, WR 47%
    atr_period 16 sits in the MIDDLE of a broad plateau (10..24 all healthy), which is why it
    was chosen over the marginally different 14 or 20 â€” plateau centre beats plateau peak.

    THINGS THE OPERATOR MUST KNOW (research/eterna_concentration.py):
      - Profit is EXTREMELY concentrated: the 10 best trades (1.7%) produce 85% of all profit;
        the MEDIAN trade LOSES $4.67 and win-rate is only 37%. Miss the 5 best trades and 58%
        of the profit is gone. THE SLOT MUST BE LEFT RUNNING through flat months.
      - Monthly PnL is NOT smooth and cannot be made smooth: lowering TP to 1:1 evens the curve
        but cuts net by 70% and does NOT raise the share of green months (44% vs 53%).
      - ~47% of months are red; longest red streak 5 months.
      - 91% of backtest profit came from 2024-2026 (gold trending). Block-bootstrap over the
        flat 2021-2023 regime: P(loss) 33.6%. In a flat regime it survives, it does not earn.
      - Single trades have risked up to 33% of a $1000 account (median 5.6%). The structure
        stop widens with volatility and 0.01 is the minimum lot, so on a small account the only
        control is to SKIP the trade â€” that is what `_risk_ok()` does.

    Restart-safe: `_reconcile()` runs every poll â€” it adopts an existing MT5 position on the
    first pass and afterwards detects a broker SL/TP close, so the brain never thinks it holds
    a position the broker already closed. One position at a time.
    """

    def __init__(self, spec: dict, cfg: dict, data: DataProvider):
        super().__init__(spec, cfg, data)
        p = spec.get("params", {})
        self.timeframe = str(p.get("timeframe", "1h"))
        self.atr_period = int(p.get("atr_period", 16))
        self.mult_entry = float(p.get("mult_entry", 1.8))
        self.mult_trend = float(p.get("mult_trend", 3.8))
        # EA line 259 ties the structure lookback to ATR_Period; keep them tied by default.
        self.struct_bars = int(p.get("struct_bars", self.atr_period))
        self.tp_ratio = float(p.get("tp_ratio", 4.0))       # 0 = no TP (EA Manual_TP_Points=0)
        self.min_sl_dist = float(p.get("min_sl_dist", 0.30))
        # --- EA-faithful switches, reconstructed from the user's real deal history ---
        #   magic 920641 traded MODE_DIRECT + SL_MANUAL $10 + no TP (28 Jul - 5 Aug 2026):
        #   it closed and reopened at the SAME second and price (always-in stop-and-reverse),
        #   and its stop-out was exactly -$10.00 at 0.01 lot. Both modes are supported so the
        #   original and the researched variant can run side by side under different magics.
        self.mode = str(p.get("mode", "conservative"))      # conservative | direct
        self.sl_mode = str(p.get("sl_mode", "structure"))   # structure | manual
        self.manual_sl_usd = float(p.get("manual_sl_usd", 10.0))  # SL_MANUAL 1000 pts @0.01
        self.history_bars = int(p.get("history_bars", 30000))   # ~500 H1 bars from M1
        self._prev_action = "FLAT"
        self._counter = 0
        self._sl = 0.0
        self._tp = 0.0
        self._last_bar_ts = None
        self._cached: SignalResponse | None = None
        self._adopted = False

    # ---------- indicators ----------
    @staticmethod
    def _atr(df: pd.DataFrame, n: int) -> pd.Series:
        pc = df["close"].shift(1)
        tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                        (df["low"] - pc).abs()], axis=1).max(axis=1)
        return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()

    def _supertrend(self, df: pd.DataFrame, mult: float) -> list:
        """+1 uptrend / -1 downtrend per bar (standard Supertrend, Wilder ATR)."""
        import math
        a = self._atr(df, self.atr_period)
        hl2 = (df["high"] + df["low"]) / 2.0
        up = (hl2 + mult * a).to_numpy()
        lo = (hl2 - mult * a).to_numpy()
        c = df["close"].to_numpy()
        n = len(df)
        fu = [float("nan")] * n
        fl = [float("nan")] * n
        d = [1] * n
        for i in range(1, n):
            if math.isnan(up[i]) or math.isnan(lo[i]):
                continue
            fu[i] = up[i] if (math.isnan(fu[i-1]) or up[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
            fl[i] = lo[i] if (math.isnan(fl[i-1]) or lo[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]
            if not math.isnan(fu[i-1]) and c[i] > fu[i]:
                d[i] = 1
            elif not math.isnan(fl[i-1]) and c[i] < fl[i]:
                d[i] = -1
            else:
                d[i] = d[i-1]
        return d

    # ---------- main ----------
    def evaluate(self) -> SignalResponse:
        now = pd.Timestamp.utcnow()
        ts = now.isoformat()
        self._reconcile()
        if not self._adopted:
            # Rekonsiliasi belum pernah berhasil -> kita TIDAK TAHU apakah ada posisi.
            # Menghitung sinyal dari ketidaktahuan itulah yang menutup posisi hidup
            # pada 2026-08-12. Tahan keadaan terakhir; poll berikutnya mencoba lagi.
            logger.warning(f"[{self.name}] belum tersinkron dengan MT5 - menahan "
                           f"'{self._prev_action}', tidak mengambil keputusan baru")
            return self._emit(self._prev_action, self._sl, self._tp, ts)
        df = self.data.recent_bars(self.symbol, self.history_bars)
        if df.empty:
            return self._emit("FLAT", 0.0, 0.0, ts)
        h = df.resample(self.timeframe, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        if len(h) < self.atr_period + self.struct_bars + 5:
            return self._emit("FLAT", 0.0, 0.0, ts)                 # warming up

        cur = now.floor(self.timeframe)
        h_c = h.iloc[:-1] if (h.index[-1] == cur and len(h) > 1) else h   # CLOSED bars only
        bar_ts = h_c.index[-1]
        if self._cached is not None and bar_ts == self._last_bar_ts:
            return self._cached                                     # one decision per H1 bar
        self._last_bar_ts = bar_ts

        st_e = self._supertrend(h_c, self.mult_entry)
        st_t = self._supertrend(h_c, self.mult_trend)
        if len(st_e) < 2:
            return self._emit("FLAT", 0.0, 0.0, ts)

        flipped = st_e[-1] != st_e[-2]          # entry Supertrend flipped on the last CLOSED bar
        s = st_e[-1]
        aligned = st_t[-1] == s
        side = "BUY" if s == 1 else "SELL"

        if self.mode == "direct":
            # MODE_DIRECT: always-in stop-and-reverse, no trend gate. Emitting the opposite
            # side is enough — the EA's ReconcileTo closes and reopens in one step, which is
            # exactly what magic 920641 did (close + open at the same second and price).
            # After a broker SL the signal_id is unchanged, so the EA does NOT re-enter until
            # the next genuine flip — matching the observed 5 Aug stop-out with no re-entry.
            if not flipped and self._prev_action == "FLAT":
                return self._emit("FLAT", 0.0, 0.0, ts)
            if side == self._prev_action:
                return self._emit(side, self._sl, self._tp, ts)        # hold
        else:
            # opposite signal closes an open position, exactly like the backtest
            if self._prev_action in ("BUY", "SELL") and flipped and side != self._prev_action:
                logger.info(f"[{self.name}] opposite flip -> close {self._prev_action}")
                return self._emit("FLAT", 0.0, 0.0, ts)
            if self._prev_action in ("BUY", "SELL"):
                return self._emit(self._prev_action, self._sl, self._tp, ts)  # hold
            if not flipped or not aligned:
                return self._emit("FLAT", 0.0, 0.0, ts)

        px = float(h_c["close"].iloc[-1])
        if self.sl_mode == "manual":
            # SL_MANUAL: fixed distance in account currency (EA: Manual_SL_Points * _Point).
            # XAUUSD on FBS is 2 digits, point 0.01, contract 100 -> 1000 points = $10.00.
            dist = self.manual_sl_usd
        else:
            window = h_c.iloc[-self.struct_bars:]   # last N CLOSED bars incl. the signal bar
            raw = float(window["low"].min()) if s == 1 else float(window["high"].max())
            dist = abs(px - raw)
        if dist < self.min_sl_dist:
            logger.info(f"[{self.name}] stop too tight ({dist:.2f}) -> skip")
            return self._emit("FLAT", 0.0, 0.0, ts)
        sl = px - dist if s == 1 else px + dist
        if self.tp_ratio > 0:
            tp = px + self.tp_ratio * dist if s == 1 else px - self.tp_ratio * dist
        else:
            tp = 0.0                                # no TP; exit is the reverse signal or SL

        mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]
        if _book_conflict(mt5_symbol, side, self.magic):
            logger.info(f"[{self.name}] net-exposure guard: another slot already {side}")
            return self._emit("FLAT", 0.0, 0.0, ts)
        if not _risk_ok(px, sl, float(self.lot), mt5_symbol):
            logger.info(f"[{self.name}] risk cap: stop too wide ({dist:.2f}) -> skip")
            return self._emit("FLAT", 0.0, 0.0, ts)

        logger.info(f"[{self.name}] {side} @ {px:.2f} sl={sl:.2f} tp={tp:.2f} "
                    f"risk=${dist * float(self.lot) * 100:.2f}")
        return self._emit(side, sl, tp, ts)

    def _emit(self, action: str, sl: float, tp: float, ts: str) -> SignalResponse:
        action = _governed(action, self._prev_action)        # monthly profit governor gate
        if action != self._prev_action:
            self._counter += 1
            self._prev_action, self._sl, self._tp = action, sl, tp
        if action == "FLAT":
            self._cached = self._flat(str(self._counter), ts)
        else:
            self._cached = SignalResponse(
                strategy=self.name, symbol=self.symbol, action=action,
                sl=round(self._sl, 3), tp=round(self._tp, 3), lot=float(self.lot),
                magic=self.magic, signal_id=f"{self.symbol}-{self.name}-{self._counter}", ts=ts,
            )
        return self._cached

    def _reconcile(self) -> None:
        """Every poll: adopt an existing position, and notice when the broker closed one.

        Without this the brain would keep believing it holds a position that the broker's
        SL/TP already closed, and would never take the next entry.
        """
        try:
            import MetaTrader5 as mt5
            # ---------------------------------------------------------------------------
            #  INSIDEN 2026-08-12: restart brain MENUTUP posisi eterna yang sedang terbuka.
            #  Runtutannya: 16:32:58 slot dimuat -> 16:32:59 executor menutup posisi.
            #
            #  Sebabnya urutan di evaluate(): _reconcile() dipanggil SEBELUM
            #  self.data.recent_bars(), dan recent_bars itulah yang meng-inisialisasi MT5 di
            #  proses brain. Jadi pada poll PERTAMA setelah restart, positions_get()
            #  mengembalikan None, tidak ada yang diadopsi, _prev_action tetap "FLAT", lalu
            #  strategi meng-emit FLAT dengan signal_id baru -> executor menutup posisi.
            #
            #  DUA perbaikan, keduanya perlu:
            #    1. initialize() di sini - idempoten, True kalau sudah tersambung. Ini
            #       menghapus ketergantungan pada urutan pemanggilan.
            #    2. Guard positions_get() is None -> JANGAN tandai _adopted. Pola ini sudah
            #       ada di ZRevStrategy._reconcile_position() sejak lama ("do NOT mark
            #       reconciled / emit FLAT and close a live leg") tapi tidak pernah
            #       diterapkan ke sini.
            #
            #  Bahayanya bukan kerugian $5,38 hari itu, tapi bahwa watchdog me-restart brain
            #  secara OTOMATIS saat crash - artinya tiap pemulihan otomatis akan meratakan
            #  posisi terbuka.
            # ---------------------------------------------------------------------------
            if not mt5.initialize():
                logger.warning(f"[{self.name}] MT5 belum siap saat reconcile - "
                               f"coba lagi poll berikutnya (posisi TIDAK disentuh)")
                return

            mt5_symbol = self.cfg["symbols"][self.symbol]["mt5_symbol"]
            raw = mt5.positions_get(symbol=mt5_symbol)
            if raw is None:
                logger.warning(f"[{self.name}] positions_get None saat reconcile - "
                               f"coba lagi poll berikutnya (posisi TIDAK disentuh)")
                return                      # JANGAN tandai _adopted; jangan pernah emit FLAT

            mine = [p for p in raw if p.magic == self.magic]
            if mine:
                p = mine[0]
                act = "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"
                if not self._adopted or act != self._prev_action:
                    self._prev_action, self._sl, self._tp = act, float(p.sl), float(p.tp)
                    logger.info(f"[{self.name}] adopted live position {act} "
                                f"sl={p.sl} tp={p.tp}")
            elif self._prev_action in ("BUY", "SELL"):
                logger.info(f"[{self.name}] broker closed {self._prev_action} "
                            f"(SL/TP hit) -> flat")
                self._prev_action, self._sl, self._tp = "FLAT", 0.0, 0.0
                self._counter += 1
                self._cached = None
            self._adopted = True            # hanya setelah MT5 BENAR-BENAR menjawab
        except Exception as e:
            # Sengaja TIDAK menandai _adopted: kalau MT5 error, poll berikutnya coba lagi.
            logger.warning(f"[{self.name}] reconcile gagal, coba lagi ({e})")


STRATEGY_TYPES = {
    "dummy": DummyStrategy,
    "orb": ORBStrategy,
    "vision": VisionStrategy,
    "zrev": ZRevStrategy,        # Z Strategy (Donchian stop-and-reverse)
    "mr": MeanReversionStrategy, # Mean-reversion z-score fade (diversifier)
    "liqlimit": LiquidityLimitStrategy,  # Liquidity Limit (Supertrend band 'limit' entries)
    "golden": GoldenStrategy,    # Golden Strategy 1 (fade-with-trend M5 XAU + ADX skip)
    "eterna": EternaStrategy,    # Eterna (dual-Supertrend H1 XAU, conservative gate, TP 1:4)
}


# Tipe yang DIMILIKI PENUH oleh manager terpisah, bukan oleh brain. Brain harus
# MELEWATINYA, bukan membangunnya dan bukan pula mati karenanya.
#
# Ditambahkan 2026-08-13 setelah uji bunuh-rantai menemukan kegagalan yang BERBAHAYA:
# slot `smclimit` (enabled: true, dijalankan smc_limit_manager) membuat SignalEngine
# melempar ValueError saat start -> brain CRASH dan tidak pernah naik lagi. Selama
# beberapa jam /health tetap hijau semata-mata karena proses brain LAMA masih hidup
# dari sebelum slot itu ditambahkan. Kalau VPS reboot, brain tidak akan pernah kembali
# dan eterna_xau berhenti diam-diam - tanpa satu pun tanda di /health.
#
# Slot manager memakai `enabled: true` karena manager-nya membaca flag itu untuk
# memutuskan slot mana yang dijalankan; jadi brain tidak bisa mengandalkan
# `enabled: false` seperti pada liqlimit yang sudah pensiun.
MANAGER_OWNED_TYPES = {"smclimit"}   # pipeline/live/smc_limit_manager.py


class SignalEngine:
    """Builds strategy slots from config and evaluates all slots for a symbol."""

    def __init__(self, cfg: dict | None = None, data: DataProvider | None = None):
        self.cfg = cfg or load_config()
        self.data = data or DataProvider(self.cfg)
        self.strategies: list[BaseStrategy] = []
        for spec in self.cfg["live"]["strategies"]:
            if not spec.get("enabled", True):        # archived/disabled slots skipped (e.g. Z off)
                continue
            if spec["type"] in MANAGER_OWNED_TYPES:
                logger.info(f"Slot '{spec.get('name')}' (type={spec['type']}) dimiliki "
                            f"manager terpisah - brain melewatinya.")
                continue
            cls = STRATEGY_TYPES.get(spec["type"])
            if cls is None:
                raise ValueError(f"Unknown strategy type: {spec['type']!r}")
            self.strategies.append(cls(spec, self.cfg, self.data))
        logger.info(f"Loaded {len(self.strategies)} strategy slot(s): "
                    + ", ".join(f"{s.name}({s.spec['type']}->{s.symbol})" for s in self.strategies))

    def evaluate(self, symbol: str) -> list[SignalResponse]:
        return [s.evaluate() for s in self.strategies if s.symbol == symbol]
