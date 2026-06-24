"""Signal engine: a pluggable registry of strategy "slots".

Each slot in `config.yaml live.strategies` becomes one independent strategy that
emits a desired-state `SignalResponse`. The server returns all slots for a symbol
as a list, so multiple models can run concurrently (each tagged by its own
`magic`). Adding a new model = add a class to `STRATEGY_TYPES` + a config entry;
the EA never changes.

Idempotency: `action` is the position the slot *should hold*; `signal_id` is stable
for the life of one signal. The EA acts only when `signal_id` changes, and the
broker's SL/TP closes the trade (the EA won't reopen — the signal_id was acted on).
"""
import pandas as pd
from loguru import logger

from ..fetch.base_fetcher import load_config
from ..backtest.strategy_orb import ORBParams, generate_signals
from ..vision.analyzer import VisionAnalyzer
from ..vision.capture import ChartCapturer
from ..vision.journal import VisionJournal
from ..vision.state import SlotState
from .contracts import SignalResponse, flat
from .data import DataProvider

_DUMMY_CYCLE = ["FLAT", "BUY", "FLAT", "SELL"]  # one phase per minute


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
        range_minutes = p.get("range_minutes", oc["range_minutes"])
        use_sl = p.get("use_sl", True)
        range_filter = p.get("range_filter", False)         # skip abnormal-size opening ranges
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

        trades = generate_signals(df, self.symbol, session, sess["open"], params)
        if not trades:
            return self._flat(f"{date_tag}-{session}-NOBREAK", now.isoformat())

        t = trades[0]

        # live outcome: once price has touched SL/TP the trade is OVER (matches the
        # backtest, which exits there). Without this the slot keeps emitting BUY/SELL
        # all session — and if price has whipsawed past the SL, the EA spams the broker
        # with an already-underwater stop ("invalid stops", err 10016).
        done = self._exit_hit(df, t, use_sl)
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

    def _exit_hit(self, df, t, use_sl) -> str | None:
        """Has the live price touched the trade's SL/TP since entry? Returns the exit
        reason ("SL"/"TP") if the trade is over, else None — so the slot can go FLAT
        instead of chasing a finished (possibly stopped-out) trade."""
        post = df[df.index >= t.entry_ts]
        if post.empty:
            return None
        for _, bar in post.iterrows():
            if t.direction == "long":
                if use_sl and bar["low"] <= t.sl_price:
                    return "SL"
                if bar["high"] >= t.tp_price:
                    return "TP"
            else:  # short
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


class VisionStrategy(BaseStrategy):
    """AI-vision slot: screenshot a chart, ask Claude (SMC), emit a SignalResponse.

    Same `() -> SignalResponse` contract as ORB, so SignalEngine treats it
    identically. Cadence-gated (the Claude call runs once per `interval_minutes`;
    every other poll serves the cached decision, preserving signal_id and thus EA
    idempotency). FAIL-SAFE: evaluate() never raises — any error degrades to the
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
        # it must clear a (>=) higher confidence bar than a plain open — hysteresis
        # against flip-flopping on noise. Defaults to the entry bar (no extra gate).
        self.min_reverse_conf = int(p.get("min_reverse_confidence", self.min_conf))
        # Rule-based lock-profit reversal (NO Claude, runs every poll): once an open
        # position is in profit >= lock_min_profit_r, close it the moment price breaks
        # the swing of the last `reversal_lookback` completed `reversal_tf` bars
        # against the trade — banks profit fast without burning tokens. Entry stays
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
            # 0. lock-profit reversal — rule-based, NO Claude, runs every poll. Only
            #    acts on an in-profit open position; closes it on a structure flip so
            #    gains are banked before price retraces. Works off-hours/between
            #    Claude cycles, costs zero tokens.
            locked = self._lock_profit_check(now)
            if locked is not None:
                return locked

            # 1. active-hours gate — outside the configured trading windows we
            #    never call Claude (zero tokens). Serve the cached decision so an
            #    already-open position is left for the broker SL/TP to manage.
            if not self._within_active_hours(now_ts):
                return self.state.cached() or self._flat("OFFHOURS", now)

            # 1. cadence gate — between intervals, serve the cached decision so
            #    signal_id is stable and the EA does nothing.
            if not self.state.due(self.interval):
                return self.state.cached() or self._flat("BOOT", now)

            # 2. capture -> analyze (capture can raise; analyze never does)
            prev = self.state.prev_action
            bars = self.state.bars_in_state
            try:
                if self.timeframes:
                    images = self.capturer.capture_multi(self.symbol)
                    decision = self.analyzer.analyze_multi(images, self.symbol, prev, bars)
                    png = images[-1][1] if images else b""   # lowest TF frame for the journal
                else:
                    png = self.capturer.capture(self.symbol)
                    decision = self.analyzer.analyze(png, self.symbol, prev, bars)
            except Exception:
                logger.exception(f"[{self.name}] vision capture/analyze error")
                return self.state.cached() or self._flat("ERROR", now)

            # 3. ENTRY/EXIT split (best practice: guards gate ENTRIES only). An
            #    already-open position is managed by the SL/TP set at entry and is
            #    closed ONLY on an explicit Claude FLAT or a guard-clearing,
            #    high-confidence reversal — never force-closed by re-checking RR
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
                    sl, tp = self.state.cached().sl, self.state.cached().tp  # keep entry SL/TP — never widen
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
        except Exception:                       # absolute backstop — never propagate
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
            if pos is None:                      # not filled yet, or already closed — don't act
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


# register new model types here; config `type:` selects one
STRATEGY_TYPES = {
    "dummy": DummyStrategy,
    "orb": ORBStrategy,
    "vision": VisionStrategy,
}


class SignalEngine:
    """Builds strategy slots from config and evaluates all slots for a symbol."""

    def __init__(self, cfg: dict | None = None, data: DataProvider | None = None):
        self.cfg = cfg or load_config()
        self.data = data or DataProvider(self.cfg)
        self.strategies: list[BaseStrategy] = []
        for spec in self.cfg["live"]["strategies"]:
            cls = STRATEGY_TYPES.get(spec["type"])
            if cls is None:
                raise ValueError(f"Unknown strategy type: {spec['type']!r}")
            self.strategies.append(cls(spec, self.cfg, self.data))
        logger.info(f"Loaded {len(self.strategies)} strategy slot(s): "
                    + ", ".join(f"{s.name}({s.spec['type']}->{s.symbol})" for s in self.strategies))

    def evaluate(self, symbol: str) -> list[SignalResponse]:
        return [s.evaluate() for s in self.strategies if s.symbol == symbol]
