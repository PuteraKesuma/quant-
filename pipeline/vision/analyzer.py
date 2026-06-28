"""Claude-vision analyzer: chart PNG + SMC system prompt -> raw decision dict.

Sends the screenshot as a base64 image block plus a runtime-context user message
to the Claude API, then strictly parses the JSON reply. It knows nothing about
MT5, magic numbers, lots, or signal_ids — it returns only the model's decision.

Fail-safe contract: `analyze()` NEVER raises. On any failure (API down, timeout,
unparseable output, bad shape) it returns a safe FLAT dict whose `reason`
describes the failure, so the caller can degrade gracefully and the server never
500s because of vision.
"""
import base64
import json
import re
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger


class VisionAnalyzer:
    """Wraps the Claude API call + JSON parsing for one vision slot."""

    def __init__(self, spec: dict, cfg: dict):
        self.spec = spec
        self.cfg = cfg
        self.symbol = spec["symbol"]
        p = spec.get("params", {})
        vcfg = cfg.get("vision", {}) or {}
        self.model = p.get("model", "claude-opus-4-8")
        self.max_tokens = int(p.get("max_tokens", vcfg.get("max_tokens", 1024)))
        self.prompt_file = p.get("prompt_file", "pipeline/vision/prompt.md")
        self.price_offset = float(p.get("price_offset", 0.0))   # static fallback (manual)
        self.capture_mode = p.get("capture_mode", "mt5")
        # safety clamp for the dynamic TV->broker offset: ignore an offset larger than
        # this fraction of price (guards against a grossly mis-read chart_price).
        self.max_offset_frac = float(p.get("max_offset_frac", 0.01))
        self._system: str | None = None        # cached after first read
        self._client = None                     # lazy; tests may inject a mock
        load_dotenv()                           # populate ANTHROPIC_API_KEY from .env

    # ------------------------------------------------------------------ public
    def analyze(self, png: bytes, symbol: str, prev_action: str,
                bars_in_state: int, broker_price: float | None = None) -> dict:
        """Return the model's decision dict. Never raises — safe FLAT on error.
        `broker_price` (live FBS price) drives the TV->broker SL/TP correction."""
        try:
            b64 = base64.standard_b64encode(png).decode("utf-8")
            content = [
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": self._user_text(symbol, prev_action, bars_in_state,
                                                          broker_price=broker_price)},
            ]
            return self._call(content, symbol, broker_price)
        except Exception as e:                  # fail-safe: never propagate
            logger.exception(f"[vision:{symbol}] analyze failed")
            return self._safe_flat(f"analyze error: {e}")

    def analyze_multi(self, images: list[tuple[str, bytes]], symbol: str,
                      prev_action: str, bars_in_state: int,
                      broker_price: float | None = None) -> dict:
        """Like analyze() but sends several timeframe images (highest->lowest) in
        one call so the model can use HTF bias + LTF entry. Never raises."""
        try:
            tfs = [label for label, _ in images]
            content: list[dict] = []
            for label, png in images:
                b64 = base64.standard_b64encode(png).decode("utf-8")
                content.append({"type": "text", "text": f"Chart timeframe {label}:"})
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": b64}})
            content.append({"type": "text",
                            "text": self._user_text(symbol, prev_action, bars_in_state, tfs,
                                                    broker_price=broker_price)})
            return self._call(content, symbol, broker_price)
        except Exception as e:                  # fail-safe: never propagate
            logger.exception(f"[vision:{symbol}] analyze_multi failed")
            return self._safe_flat(f"analyze error: {e}")

    # ----------------------------------------------------------------- helpers
    def _call(self, content: list[dict], symbol: str,
              broker_price: float | None = None) -> dict:
        """Send the prepared content blocks to Claude, parse + log. May raise."""
        client = self._get_client()
        resp = client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=self._system_prompt(),
            messages=[{"role": "user", "content": content}],
        )
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        decision = self._apply_offset(self._parse(raw), broker_price)
        logger.info(
            f"[vision:{symbol}] action={decision['action']} conf={decision['confidence']} "
            f"chart_px={decision.get('chart_price')} off={decision.get('offset_applied', 0)} "
            f"sl={decision['sl']} tp={decision['tp']}"
        )
        return decision

    def _user_text(self, symbol: str, prev_action: str, bars_in_state: int,
                   tfs: list[str] | None = None, broker_price: float | None = None) -> str:
        """Runtime-context user message. With `tfs` it frames the multi-TF read."""
        if tfs:
            head = (
                f"You are given {len(tfs)} chart images of the SAME symbol at different "
                f"timeframes ({', '.join(tfs)}), ordered highest to lowest. Use the higher "
                "timeframe(s) for directional bias and the major OB/FVG/IFVG zones, and the "
                "lowest timeframe for entry timing and precise SL/TP placement. Trade only "
                "when the timeframes ALIGN.\n\n"
            )
        else:
            head = "Analyze this chart. "
        # Report chart_price = the latest price visible on the LOWEST-TF chart (the 'C'
        # value in the legend). The execution broker's feed may differ by a small
        # constant; the system corrects SL/TP automatically using chart_price, so read
        # ALL levels straight off the chart's own price axis.
        anchor = ""
        if broker_price:
            anchor = (f"- Broker live price (for reference only; do NOT shift your reads): "
                      f"{broker_price}\n")
        return (
            head + "Runtime context:\n"
            f"- ServerSymbol: {symbol}\n"
            f"{anchor}"
            f"- Current open slot action (previous decision): {prev_action}\n"
            f"- Slot has been in this state for: {bars_in_state} candles\n\n"
            "Read sl, tp AND chart_price off the chart's own price axis. "
            "Decide the desired end state now."
        )

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
        return self._client

    def _system_prompt(self) -> str:
        if self._system is None:
            text = Path(self.prompt_file).read_text(encoding="utf-8")
            # The file may bundle a "## SYSTEM PROMPT" section and a
            # "## USER MESSAGE" template; use only the system-prompt body.
            if "## SYSTEM PROMPT" in text:
                text = text.split("## SYSTEM PROMPT", 1)[1]
            if "## USER MESSAGE" in text:
                text = text.split("## USER MESSAGE", 1)[0]
            self._system = text.strip()
        return self._system

    def _parse(self, raw: str) -> dict:
        """Strip code fences / prose, json.loads, validate shape + action enum.
        Raises on malformed output (caught by `analyze` -> safe FLAT)."""
        s = (raw or "").strip()
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)
        if m:
            s = m.group(1)
        else:
            m2 = re.search(r"\{.*\}", s, re.DOTALL)   # first bare {...} object
            if m2:
                s = m2.group(0)
        data = json.loads(s)
        if not isinstance(data, dict):
            raise ValueError("decision is not a JSON object")

        action = str(data.get("action", "FLAT")).upper()
        if action not in ("BUY", "SELL", "FLAT"):
            raise ValueError(f"invalid action: {action!r}")

        kl = data.get("key_levels")
        return {
            "action": action,
            "confidence": int(float(data.get("confidence", 0) or 0)),
            "sl": float(data.get("sl", 0) or 0),
            "tp": float(data.get("tp", 0) or 0),
            "chart_price": float(data.get("chart_price", 0) or 0),  # latest price read off chart
            "reason": str(data.get("reason", "")),
            "structure": str(data.get("structure", "")),
            "key_levels": kl if isinstance(kl, dict) else {},
        }

    def _apply_offset(self, d: dict, broker_price: float | None = None) -> dict:
        """Correct sl/tp from chart prices to BROKER prices.

        TradingView capture: the chart feed differs from the broker by a small,
        slowly-varying constant. We measure it live as `broker_price - chart_price`
        (both from the same instant) and shift sl/tp by it, so orders land at broker
        prices. A grossly mis-read chart_price (offset > max_offset_frac of price) is
        rejected and the levels are left unshifted (fail-safe). Other modes use the
        static `price_offset` (0.0 by default)."""
        if d["action"] == "FLAT":
            return d
        if self.capture_mode == "tradingview" and broker_price and d.get("chart_price"):
            off = broker_price - d["chart_price"]
            if abs(off) <= self.max_offset_frac * broker_price:
                d["sl"] = round(d["sl"] + off, 5)
                d["tp"] = round(d["tp"] + off, 5)
                d["offset_applied"] = round(off, 5)
            else:
                logger.warning(
                    f"[vision:{self.symbol}] dynamic offset {off:.3f} exceeds "
                    f"{self.max_offset_frac:.1%} of {broker_price} — NOT applied "
                    f"(chart_price {d['chart_price']} likely mis-read); sl/tp unshifted")
            return d
        if self.price_offset:                       # static fallback (manual)
            d["sl"] = round(d["sl"] + self.price_offset, 5)
            d["tp"] = round(d["tp"] + self.price_offset, 5)
        return d

    def _safe_flat(self, reason: str) -> dict:
        return {"action": "FLAT", "confidence": 0, "sl": 0.0, "tp": 0.0,
                "reason": reason, "structure": "", "key_levels": {}}
