"""Tests for MeanReversionStrategy: registry + signal_id idempotency lifecycle."""
from pipeline.live.signal import MeanReversionStrategy, STRATEGY_TYPES


def _mk():
    s = MeanReversionStrategy.__new__(MeanReversionStrategy)
    s.name = "mr_xau"; s.symbol = "XAUUSD"; s.magic = 920623; s.lot = 0.01
    s._prev_action = "FLAT"; s._counter = 0; s._sl = 0.0; s._tp = 0.0; s._entry_ts = None
    return s


def test_mr_registered():
    assert STRATEGY_TYPES.get("mr") is MeanReversionStrategy


def test_emit_idempotent_while_holding():
    s = _mk()
    r1 = s._emit("BUY", 1990.0, 2000.0, "t")
    r2 = s._emit("BUY", 1990.0, 2000.0, "t")     # still holding -> same signal_id
    assert r1.signal_id == r2.signal_id
    assert r1.action == "BUY" and r1.tp == 2000.0 and r1.sl == 1990.0 and r1.magic == 920623


def test_exit_changes_signal_id_and_resets():
    s = _mk()
    s._emit("SELL", 2010.0, 2000.0, "t")
    assert s._sl == 2010.0 and s._tp == 2000.0
    r = s._emit("FLAT", 0.0, 0.0, "t")           # broker/time exit
    assert r.action == "FLAT"
    assert s._sl == 0.0 and s._tp == 0.0 and s._entry_ts is None
    assert r.signal_id.endswith("MR-2")          # counter advanced on the change
