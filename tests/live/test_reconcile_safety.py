"""Regression tests: a brain that cannot see the book must never say FLAT.

FLAT is an INSTRUCTION -- the EA carries it out by closing. So "I cannot read your
positions right now" must never be delivered as FLAT, or a restart closes live
trades.

This is not hypothetical. On 2026-08-21 a brain restart closed eterna's open BUY
at +2.19 with reason=3 (DEAL_REASON_EXPERT) while its TP sat far away at 4777.44.
Cause: EternaStrategy._reconcile did `mt5.positions_get(...) or []`, so None --
which MT5 returns while it is still starting up -- was read as "no positions".

Two behaviours are locked in here:
  1. positions_get() -> None leaves _adopted False (state unknown, do not guess)
  2. SignalEngine.evaluate WITHHOLDS the signal of any slot that is not _adopted,
     so the EA receives nothing and holds what it has
"""
import pipeline.live.signal as signal_mod
from pipeline.live.signal import EternaStrategy


class _FakeMT5:
    """Stands in for the MetaTrader5 module inside _reconcile."""

    POSITION_TYPE_BUY = 0

    def __init__(self, result):
        self._result = result

    def positions_get(self, **_):
        return self._result


def _mk(monkeypatch, mt5_result):
    s = EternaStrategy.__new__(EternaStrategy)
    s.name = "eterna_xau"
    s.symbol = "XAUUSD"
    s.magic = 920627
    s.cfg = {"symbols": {"XAUUSD": {"mt5_symbol": "XAUUSD"}}}
    s._prev_action = "FLAT"
    s._sl = 0.0
    s._tp = 0.0
    s._counter = 0
    s._cached = None
    s._adopted = False
    monkeypatch.setitem(__import__("sys").modules, "MetaTrader5", _FakeMT5(mt5_result))
    return s


def test_none_from_positions_get_leaves_state_unadopted(monkeypatch):
    """MT5 not ready -> we must NOT conclude 'flat'."""
    s = _mk(monkeypatch, None)
    s._reconcile()
    assert s._adopted is False, "None must not be read as 'no positions'"
    assert s._prev_action == "FLAT"          # untouched, still unknown


def test_empty_tuple_is_a_real_flat(monkeypatch):
    """MT5 answered and the book is genuinely empty -> adopted, safe to act."""
    s = _mk(monkeypatch, ())
    s._reconcile()
    assert s._adopted is True


def test_engine_withholds_signal_until_book_is_read():
    """An un-adopted slot contributes NO signal, so the EA holds instead of closing."""

    class _Slot:
        name = "eterna_xau"
        symbol = "XAUUSD"
        _adopted = False

        def evaluate(self):
            return "FLAT-signal-that-must-not-escape"

    eng = signal_mod.SignalEngine.__new__(signal_mod.SignalEngine)
    eng.strategies = [_Slot()]
    assert eng.evaluate("XAUUSD") == []

    eng.strategies[0]._adopted = True         # book read -> normal service resumes
    assert eng.evaluate("XAUUSD") == ["FLAT-signal-that-must-not-escape"]


def test_slots_without_the_attribute_are_not_withheld():
    """Other strategy classes use their own reconcile flag; they must keep working."""

    class _Other:
        name = "other"
        symbol = "XAUUSD"

        def evaluate(self):
            return "sig"

    eng = signal_mod.SignalEngine.__new__(signal_mod.SignalEngine)
    eng.strategies = [_Other()]
    assert eng.evaluate("XAUUSD") == ["sig"]
