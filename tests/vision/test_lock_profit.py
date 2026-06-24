"""Rule-based lock-profit reversal exit (no Claude):
- _is_reversal: pure swing-break rule,
- _reversal_hit: profit gate (>= lock_min_profit_r) + structure flip,
- _lock_profit_check: only acts on a held, in-profit position; returns FLAT.
"""
from types import SimpleNamespace
import pandas as pd
import MetaTrader5 as mt5
from pipeline.live.signal import VisionStrategy


class _Data:
    def __init__(self, m1=None): self.m1 = m1
    def recent_bars(self, symbol, n): return self.m1 if self.m1 is not None else pd.DataFrame({"close": [4100.0]})


def _make(lock=True, min_r=0.5):
    spec = {"name": "vision_smc_xau", "symbol": "XAUUSD", "lot": 0.01, "magic": 920621,
            "params": {"lock_profit_reversal": lock, "lock_min_profit_r": min_r,
                       "reversal_tf": "M5", "reversal_lookback": 3,
                       "capture_mode": "mt5", "prompt_file": "pipeline/vision/prompt.md"}}
    cfg = {"vision": {}, "symbols": {"XAUUSD": {"mt5_symbol": "XAUUSD"}}}
    return VisionStrategy(spec, cfg, _Data())

def _bars(hi, lo):
    return pd.DataFrame({"high": [hi, hi - 1, hi - 2], "low": [lo, lo + 1, lo + 2]})

def _pos(direction, entry, sl, current):
    t = mt5.POSITION_TYPE_BUY if direction == "BUY" else mt5.POSITION_TYPE_SELL
    return SimpleNamespace(type=t, price_open=entry, sl=sl, price_current=current, magic=920621)


# ---- pure rule ----
def test_is_reversal_sell_breaks_above_swing_high():
    assert VisionStrategy._is_reversal("SELL", 4091.0, _bars(hi=4090, lo=4080)) is True
    assert VisionStrategy._is_reversal("SELL", 4089.0, _bars(hi=4090, lo=4080)) is False

def test_is_reversal_buy_breaks_below_swing_low():
    assert VisionStrategy._is_reversal("BUY", 4079.0, _bars(hi=4090, lo=4080)) is True
    assert VisionStrategy._is_reversal("BUY", 4081.0, _bars(hi=4090, lo=4080)) is False


# ---- profit gate + structure ----
def test_reversal_hit_requires_min_profit_r():
    s = _make(min_r=0.5)
    s._reversal_bars = lambda: _bars(hi=4085, lo=4070)   # would flip if profit ok
    # SELL entry 4100, sl 4120 (risk 20), current 4099 -> profit 1 -> 0.05R < 0.5 -> no
    assert s._reversal_hit(_pos("SELL", 4100, 4120, 4099)) is False

def test_reversal_hit_fires_when_in_profit_and_flips():
    s = _make(min_r=0.5)
    s._reversal_bars = lambda: _bars(hi=4085, lo=4070)
    # SELL entry 4100, sl 4120 (risk 20), current 4090 -> profit 10 -> 0.5R ok; 4090 > swing high 4085 -> flip
    assert s._reversal_hit(_pos("SELL", 4100, 4120, 4090)) is True

def test_reversal_hit_holds_when_no_flip():
    s = _make(min_r=0.5)
    s._reversal_bars = lambda: _bars(hi=4095, lo=4070)   # swing high 4095 > current -> no flip
    assert s._reversal_hit(_pos("SELL", 4100, 4120, 4090)) is False


# ---- orchestration ----
def test_lock_check_noop_when_flat():
    s = _make()
    assert s.state.prev_action == "FLAT"
    assert s._lock_profit_check("t") is None

def test_lock_check_noop_when_no_open_position():
    s = _make()
    s.state.prev_action = "SELL"
    s._open_position = lambda: None
    assert s._lock_profit_check("t") is None      # not filled / already closed -> don't act

def test_lock_check_closes_on_reversal():
    s = _make()
    s.state.prev_action = "SELL"
    s._open_position = lambda: _pos("SELL", 4100, 4120, 4090)
    s._reversal_hit = lambda pos: True
    resp = s._lock_profit_check("t")
    assert resp is not None and resp.action == "FLAT"

def test_lock_check_disabled():
    s = _make(lock=False)
    s.state.prev_action = "SELL"
    s._open_position = lambda: _pos("SELL", 4100, 4120, 4090)
    assert s._lock_profit_check("t") is None       # feature off -> never acts
