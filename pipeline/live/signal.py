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
        self.archive_all = bool(p.get("archive_all_frames", False))
        self.capturer = ChartCapturer(spec, cfg)
        self.analyzer = VisionAnalyzer(spec, cfg)
        self.state = SlotState(self.symbol, self.name)
        self.journal = VisionJournal(cfg)

    def evaluate(self) -> SignalResponse:
        now = pd.Timestamp.utcnow().isoformat()
        try:
            # 1. cadence gate — between intervals, serve the cached decision so
            #    signal_id is stable and the EA does nothing.
            if not self.state.due(self.interval):
                return self.state.cached() or self._flat("BOOT", now)

            # 2. capture -> analyze (capture can raise; analyze never does)
            prev = self.state.prev_action
            bars = self.state.bars_in_state
            try:
                png = self.capturer.capture(self.symbol)
                decision = self.analyzer.analyze(png, self.symbol, prev, bars)
            except Exception:
                logger.exception(f"[{self.name}] vision capture/analyze error")
                return self.state.cached() or self._flat("ERROR", now)

            # 3. guards may override the model's action to FLAT
            action = self._apply_guards(decision)

            # 4. commit (signal_id lifecycle) + journal, then cache & return
            def builder(sig_id: str) -> SignalResponse:
                if action == "FLAT":
                    return flat(self.name, self.symbol, self.magic, sig_id, now)
                return SignalResponse(
                    strategy=self.name, symbol=self.symbol, action=action,
                    sl=round(float(decision["sl"]), 5), tp=round(float(decision["tp"]), 5),
                    lot=self.lot, magic=self.magic, signal_id=sig_id, ts=now,
                )

            resp = self.state.commit(action, builder)
            self.journal.record(self.symbol, self.name, png, decision,
                                resp.signal_id, self.state.last_changed, self.archive_all)
            return resp
        except Exception:                       # absolute backstop — never propagate
            logger.exception(f"[{self.name}] vision evaluate fatal")
            return self.state.cached() or self._flat("ERROR", now)

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
