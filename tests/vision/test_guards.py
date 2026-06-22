"""VisionStrategy._apply_guards: confidence and RR thresholds force FLAT."""
import pandas as pd

from pipeline.live.signal import VisionStrategy


class _StubData:
    """Returns a fixed last price as the entry reference for the RR guard."""
    def __init__(self, price):
        self._price = price

    def recent_bars(self, symbol, n):
        return pd.DataFrame({"close": [self._price]})


def _strategy(price=4150.0, min_conf=60, min_rr=1.5):
    spec = {"name": "vision_xau", "symbol": "XAUUSD", "lot": 0.01, "magic": 920619,
            "params": {"min_confidence": min_conf, "min_rr": min_rr,
                       "capture_mode": "mt5", "prompt_file": "pipeline/vision/prompt.md"}}
    cfg = {"vision": {}, "symbols": {"XAUUSD": {"mt5_symbol": "XAUUSD"}}}
    return VisionStrategy(spec, cfg, _StubData(price))


def test_low_confidence_forces_flat():
    s = _strategy()
    d = {"action": "BUY", "confidence": 40, "sl": 4100, "tp": 4300}  # great RR but weak conf
    assert s._apply_guards(d) == "FLAT"


def test_low_rr_forces_flat():
    s = _strategy(price=4150.0)
    # BUY: entry 4150, risk = 50 (sl 4100), reward = 30 (tp 4180) -> RR 0.6 < 1.5
    d = {"action": "BUY", "confidence": 90, "sl": 4100, "tp": 4180}
    assert s._apply_guards(d) == "FLAT"


def test_good_setup_passes():
    s = _strategy(price=4150.0)
    # risk 50, reward 100 -> RR 2.0 >= 1.5, confidence 80 >= 60
    d = {"action": "BUY", "confidence": 80, "sl": 4100, "tp": 4250}
    assert s._apply_guards(d) == "BUY"


def test_flat_decision_stays_flat():
    s = _strategy()
    d = {"action": "FLAT", "confidence": 0, "sl": 0, "tp": 0}
    assert s._apply_guards(d) == "FLAT"


def test_missing_entry_price_forces_flat():
    class _NoData:
        def recent_bars(self, symbol, n):
            return pd.DataFrame(columns=["close"])   # empty -> no entry price
    s = _strategy()
    s.data = _NoData()
    d = {"action": "SELL", "confidence": 90, "sl": 4200, "tp": 4000}
    assert s._apply_guards(d) == "FLAT"
