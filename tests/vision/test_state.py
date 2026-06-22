"""SlotState: signal_id lifecycle + cadence gate."""
from pipeline.live.contracts import SignalResponse, flat
from pipeline.vision.state import SlotState


def _builder(action):
    def build(sig_id):
        if action == "FLAT":
            return flat("vision_xau", "XAUUSD", 920619, sig_id, "ts")
        return SignalResponse(
            strategy="vision_xau", symbol="XAUUSD", action=action,
            sl=1.0, tp=2.0, lot=0.01, magic=920619, signal_id=sig_id, ts="ts",
        )
    return build


def test_same_action_keeps_signal_id():
    s = SlotState("XAUUSD", "vision_xau")
    r1 = s.commit("BUY", _builder("BUY"))
    r2 = s.commit("BUY", _builder("BUY"))
    assert r1.signal_id == r2.signal_id          # unchanged action -> stable id
    assert s.last_changed is False
    assert s.bars_in_state == 2                   # held the state for 2 analyses


def test_changed_action_increments_signal_id():
    s = SlotState("XAUUSD", "vision_xau")
    r1 = s.commit("BUY", _builder("BUY"))         # FLAT -> BUY  : counter 1
    r2 = s.commit("SELL", _builder("SELL"))       # BUY  -> SELL : counter 2
    assert r1.signal_id != r2.signal_id
    assert r1.signal_id.endswith("VIS-1")
    assert r2.signal_id.endswith("VIS-2")
    assert s.last_changed is True
    assert s.bars_in_state == 1                   # reset on change


def test_signal_id_format():
    s = SlotState("XAUUSD", "vision_xau")
    r = s.commit("BUY", _builder("BUY"))
    assert r.signal_id == "XAUUSD-vision_xau-VIS-1"


def test_due_respects_interval():
    s = SlotState("XAUUSD", "vision_xau")
    assert s.due(15) is True       # first call: due (never analysed)
    assert s.due(15) is False      # immediately after: inside the interval
    assert s.due(0) is True        # zero interval: always due


def test_cached_none_until_commit():
    s = SlotState("XAUUSD", "vision_xau")
    assert s.cached() is None
    r = s.commit("FLAT", _builder("FLAT"))
    assert s.cached() is r
