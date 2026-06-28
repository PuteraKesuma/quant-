"""VisionStrategy entry/exit policy (best practice):
- guards gate ENTRIES only (a fresh FLAT slot),
- an open position HOLDS through a degraded-RR re-check (no force close),
- its SL/TP are never widened mid-trade,
- it closes only on an explicit Claude FLAT or a high-confidence reversal.
"""
import pandas as pd
from pipeline.live.signal import VisionStrategy


class _Data:
    def __init__(self, price): self.price = price
    def recent_bars(self, symbol, n): return pd.DataFrame({"close": [self.price]})

class _Cap:
    def capture(self, s): return b"x"
    def capture_multi(self, s): return [("M5", b"x")]

class _An:
    d = None
    def analyze(self, *a, **k): return self.d
    def analyze_multi(self, *a, **k): return self.d

class _Jr:
    def record(self, *a, **k): pass


def _make(price=4100.0, min_conf=60, min_rr=1.5, min_rev=60):
    spec = {"name": "vision_smc_xau", "symbol": "XAUUSD", "lot": 0.01, "magic": 920621,
            "params": {"min_confidence": min_conf, "min_rr": min_rr,
                       "min_reverse_confidence": min_rev, "interval_minutes": 0,
                       "capture_mode": "mt5", "prompt_file": "pipeline/vision/prompt.md"}}
    cfg = {"vision": {}, "symbols": {"XAUUSD": {"mt5_symbol": "XAUUSD"}}}
    s = VisionStrategy(spec, cfg, _Data(price))
    s.capturer, s.analyzer, s.journal = _Cap(), _An(), _Jr()
    return s

def _set(s, action, conf, sl, tp):
    s.analyzer.d = {"action": action, "confidence": conf, "sl": sl, "tp": tp,
                    "reason": "", "structure": "", "key_levels": {}}


def test_entry_guard_blocks_bad_rr():
    s = _make(price=4100.0)
    _set(s, "SELL", 90, sl=4120, tp=4115)        # RR ~ 0.25 -> blocked at entry
    assert s.evaluate().action == "FLAT"

def test_entry_passes_good_setup():
    s = _make(price=4100.0)
    _set(s, "SELL", 70, sl=4120, tp=4060)        # RR 2.0, conf 70
    r = s.evaluate()
    assert r.action == "SELL" and r.sl == 4120 and r.tp == 4060

def test_open_position_holds_through_degraded_rr():
    """The bug we fixed: an open SELL must NOT be force-closed when a later
    re-check has RR < min_rr, and its SL/TP must not be widened."""
    s = _make(price=4100.0)
    _set(s, "SELL", 70, sl=4120, tp=4060)
    first = s.evaluate()                          # open SELL
    _set(s, "SELL", 70, sl=4130, tp=4128)        # still SELL but RR now ~0.4 + wider SL
    held = s.evaluate()
    assert held.action == "SELL"                  # held, not flattened
    assert held.signal_id == first.signal_id      # idempotent: EA does nothing
    assert held.sl == 4120 and held.tp == 4060    # original SL/TP kept (no widening)

def test_explicit_flat_closes():
    s = _make(price=4100.0)
    _set(s, "SELL", 70, sl=4120, tp=4060); s.evaluate()
    _set(s, "FLAT", 0, 0, 0)
    assert s.evaluate().action == "FLAT"          # Claude explicitly exits -> close

def test_weak_reversal_is_ignored():
    s = _make(price=4100.0, min_rev=75)
    _set(s, "SELL", 80, sl=4120, tp=4060); s.evaluate()
    _set(s, "BUY", 70, sl=4080, tp=4160)         # good RR but conf 70 < reverse bar 75
    assert s.evaluate().action == "SELL"          # keep the short

def test_strong_reversal_flips():
    s = _make(price=4100.0, min_rev=75)
    _set(s, "SELL", 80, sl=4120, tp=4060); s.evaluate()
    _set(s, "BUY", 80, sl=4080, tp=4160)         # conf 80 >= 75 and RR ok
    r = s.evaluate()
    assert r.action == "BUY" and r.sl == 4080 and r.tp == 4160
