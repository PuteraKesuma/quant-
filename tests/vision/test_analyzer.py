"""VisionAnalyzer: valid JSON parses; malformed output and API exceptions both
degrade to a safe FLAT dict (never raise)."""
import json

from pipeline.vision.analyzer import VisionAnalyzer


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        self.content = [_Block(text)]


class _Messages:
    def __init__(self, text, exc):
        self._text, self._exc = text, exc

    def create(self, **kwargs):
        if self._exc:
            raise self._exc
        return _Resp(self._text)


class _FakeClient:
    def __init__(self, text=None, exc=None):
        self.messages = _Messages(text, exc)


def _analyzer(text=None, exc=None, price_offset=0.0):
    spec = {"name": "vision_xau", "symbol": "XAUUSD", "lot": 0.01, "magic": 920619,
            "params": {"model": "claude-opus-4-8", "price_offset": price_offset,
                       "prompt_file": "pipeline/vision/prompt.md"}}
    a = VisionAnalyzer(spec, {"vision": {}})
    a._client = _FakeClient(text, exc)   # inject mock
    a._system = "SMC system prompt"      # skip file read
    return a


def test_valid_json_parses():
    text = json.dumps({
        "action": "BUY", "confidence": 75, "sl": 4100.0, "tp": 4200.0,
        "reason": "bullish OB at 4100", "structure": "bullish",
        "key_levels": {"resistance": 4250, "support": 4080},
    })
    d = _analyzer(text=text).analyze(b"png", "XAUUSD", "FLAT", 0)
    assert d["action"] == "BUY"
    assert d["confidence"] == 75
    assert d["sl"] == 4100.0 and d["tp"] == 4200.0
    assert d["structure"] == "bullish"
    assert d["key_levels"]["support"] == 4080


def test_fenced_json_parses():
    body = json.dumps({"action": "SELL", "confidence": 80, "sl": 4200, "tp": 4100,
                       "reason": "x", "structure": "bearish", "key_levels": {}})
    text = f"```json\n{body}\n```"
    d = _analyzer(text=text).analyze(b"png", "XAUUSD", "SELL", 1)
    assert d["action"] == "SELL"
    assert d["confidence"] == 80


def test_malformed_output_yields_flat():
    d = _analyzer(text="sorry, I cannot read this chart clearly").analyze(b"png", "XAUUSD", "FLAT", 0)
    assert d["action"] == "FLAT"
    assert d["confidence"] == 0
    assert d["sl"] == 0.0 and d["tp"] == 0.0
    assert "error" in d["reason"].lower()


def test_bad_action_enum_yields_flat():
    text = json.dumps({"action": "MAYBE", "confidence": 90, "sl": 1, "tp": 2})
    d = _analyzer(text=text).analyze(b"png", "XAUUSD", "FLAT", 0)
    assert d["action"] == "FLAT"


def test_api_exception_yields_flat():
    d = _analyzer(exc=RuntimeError("api down")).analyze(b"png", "XAUUSD", "FLAT", 0)
    assert d["action"] == "FLAT"
    assert "api down" in d["reason"]


def test_price_offset_applied():
    text = json.dumps({"action": "BUY", "confidence": 70, "sl": 4100.0, "tp": 4200.0,
                       "reason": "x", "structure": "bullish", "key_levels": {}})
    d = _analyzer(text=text, price_offset=1.5).analyze(b"png", "XAUUSD", "FLAT", 0)
    assert d["sl"] == 4101.5 and d["tp"] == 4201.5
