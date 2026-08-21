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


# --- stop-side guard -------------------------------------------------------
# MeanReversionStrategy anchors its stop to the MEAN, not to the entry, so the
# stop lands on the wrong side whenever price runs past stop_z. On XAUUSD H1
# 2015-2026 that was 55.6% of BUY signals at entry_z 2.5 / stop_z 3.0, and 100%
# at entry_z 3.0. A backtest scores a wrong-side stop as an instant WIN: with the
# bug the strategy showed +$7936 and 6/6 green years; with the stop side enforced
# every configuration tested LOSES. These lock the invariant.
from pipeline.live.signal import _stop_side_ok


def test_long_needs_stop_below_and_target_above():
    assert _stop_side_ok("BUY", sl=1990.0, px=2000.0, tp=2010.0)
    assert not _stop_side_ok("BUY", sl=2001.0, px=2000.0, tp=2010.0)   # stop above entry
    assert not _stop_side_ok("BUY", sl=2000.0, px=2000.0, tp=2010.0)   # stop AT entry
    assert not _stop_side_ok("BUY", sl=1990.0, px=2000.0, tp=1999.0)   # target below entry


def test_short_needs_stop_above_and_target_below():
    assert _stop_side_ok("SELL", sl=2010.0, px=2000.0, tp=1990.0)
    assert not _stop_side_ok("SELL", sl=1999.0, px=2000.0, tp=1990.0)  # stop below entry
    assert not _stop_side_ok("SELL", sl=2010.0, px=2000.0, tp=2001.0)  # target above entry


def test_flat_is_always_allowed():
    assert _stop_side_ok("FLAT", sl=0.0, px=2000.0, tp=0.0)
