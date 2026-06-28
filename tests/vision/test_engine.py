"""Integration: a SignalEngine with an orb + vision slot on XAUUSD returns BOTH
signals and never raises, even when the vision pipeline errors."""
import pandas as pd

from pipeline.live.signal import SignalEngine


class _StubData:
    """n==1 -> a price (vision entry ref); otherwise an empty intraday frame so
    the ORB slot returns a flat (NODATA/PENDING/CLOSED) without touching MT5."""
    def recent_bars(self, symbol, n):
        if n == 1:
            return pd.DataFrame({"close": [4150.0]})
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                            index=pd.DatetimeIndex([], tz="UTC"))


def _cfg(tmp_path):
    return {
        "symbols": {"XAUUSD": {
            "type": "commodity", "pip_size": 0.01, "mt5_symbol": "XAUUSD",
            "sessions": {"new_york": {"open": "13:30", "close": "14:30", "tz": "UTC"}},
        }},
        "orb": {"range_minutes": 30, "tp_multiplier": 1.0, "sl_multiplier": 1.0,
                "max_trades_per_session": 1, "entry_buffer_pips": 0},
        "live": {"recent_bars": 600, "strategies": [
            {"name": "orb30_xau", "type": "orb", "symbol": "XAUUSD",
             "session": "new_york", "lot": 0.01, "magic": 920618,
             "params": {"range_minutes": 30, "tp_mult": 3.0, "sl_mult": 1.0,
                        "use_sl": True, "session_end_utc": "20:00"}},
            {"name": "vision_xau", "type": "vision", "symbol": "XAUUSD",
             "lot": 0.01, "magic": 920619,
             "params": {"interval_minutes": 15, "capture_mode": "mt5",
                        "chart_timeframe": "M5", "chart_bars": 200,
                        "model": "claude-opus-4-8", "min_confidence": 60, "min_rr": 1.5,
                        "price_offset": 0.0, "prompt_file": "pipeline/vision/prompt.md",
                        "archive_all_frames": False}},
        ]},
        "vision": {"journal_path": str(tmp_path / "journal.jsonl"),
                   "archive_dir": str(tmp_path / "arc")},
    }


def _engine(tmp_path):
    return SignalEngine(_cfg(tmp_path), data=_StubData())


def _vision_slot(engine):
    return next(s for s in engine.strategies if s.name == "vision_xau")


def test_engine_returns_orb_and_vision(tmp_path):
    engine = _engine(tmp_path)
    vis = _vision_slot(engine)
    vis.capturer.capture = lambda symbol: b"PNGBYTES"
    vis.analyzer.analyze = lambda png, symbol, prev, bars, broker_price=None: {
        "action": "BUY", "confidence": 80, "sl": 4100.0, "tp": 4250.0,
        "reason": "bullish OB", "structure": "bullish", "key_levels": {},
    }

    sigs = engine.evaluate("XAUUSD")

    assert len(sigs) == 2
    names = {s.strategy for s in sigs}
    assert names == {"orb30_xau", "vision_xau"}
    vsig = next(s for s in sigs if s.strategy == "vision_xau")
    assert vsig.action == "BUY"
    assert vsig.magic == 920619
    # journal line was written
    assert (tmp_path / "journal.jsonl").exists()


def test_vision_error_does_not_break_engine(tmp_path):
    engine = _engine(tmp_path)
    vis = _vision_slot(engine)

    def _boom(symbol):
        raise RuntimeError("capture exploded")
    vis.capturer.capture = _boom

    sigs = engine.evaluate("XAUUSD")          # must not raise

    assert len(sigs) == 2                      # ORB still served
    vsig = next(s for s in sigs if s.strategy == "vision_xau")
    assert vsig.action == "FLAT"               # degraded safely


def test_cadence_caches_between_intervals(tmp_path):
    engine = _engine(tmp_path)
    vis = _vision_slot(engine)
    calls = {"n": 0}

    def _analyze(png, symbol, prev, bars, broker_price=None):
        calls["n"] += 1
        return {"action": "BUY", "confidence": 80, "sl": 4100.0, "tp": 4250.0,
                "reason": "x", "structure": "bullish", "key_levels": {}}
    vis.capturer.capture = lambda symbol: b"PNG"
    vis.analyzer.analyze = _analyze

    first = engine.evaluate("XAUUSD")
    second = engine.evaluate("XAUUSD")         # inside the 15-min interval -> cached

    assert calls["n"] == 1                      # Claude called once, not per poll
    v1 = next(s for s in first if s.strategy == "vision_xau")
    v2 = next(s for s in second if s.strategy == "vision_xau")
    assert v1.signal_id == v2.signal_id        # stable id -> EA idempotent
