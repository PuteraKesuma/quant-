"""Shadow market-context advisor — a SEPARATE, READ-ONLY process.

It watches MetaTrader5 for a NEW position opened by the live brain (matched by magic
number). On each fresh entry it captures the TradingView chart(s), asks Claude for a
macro/micro CONFIRM/CAUTION read, and appends that verdict — together with the trade's
ticket, direction and entry price — to a journal. It NEVER places, blocks, sizes, or
closes an order: it returns nothing to the brain or the EA. Pure insight.

Why a separate process: it must not touch the brain's hot /signals path (3 s EA timeout)
nor its reliability. If this crashes or the Claude API hangs, live trading is unaffected.
MT5 access is read-only (positions_get) against the already-logged-in terminal — the same
attach the brain uses (`mt5.initialize()`), a second client is fine for read calls.

Shadow contract: it annotates ONLY positions that open AFTER it starts (it seeds the set
of currently-open tickets on the first poll and skips them), so every verdict is recorded
at entry time with no lookahead. Evaluate later with research/advisor_eval.py — which joins
each verdict to the closed-trade PnL by ticket and asks: did 'CAUTION' predict losers?

Run:  python -m pipeline.live.advisor      (or START_ADVISOR.bat)
"""
import base64
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger

from pipeline.vision.tv_capture import capture_multi_tv

CONFIG = Path(__file__).resolve().parents[2] / "config.yaml"


# ----------------------------------------------------------------- Claude call
def _parse(raw: str) -> dict:
    """Pull the first JSON object out of the model reply; raise if unparseable."""
    s = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, re.DOTALL)
    if m:
        s = m.group(1)
    else:
        m2 = re.search(r"\{.*\}", s, re.DOTALL)
        if m2:
            s = m2.group(0)
    d = json.loads(s)
    verdict = str(d.get("verdict", "NEUTRAL")).upper()
    if verdict not in ("CONFIRM", "NEUTRAL", "CAUTION"):
        verdict = "NEUTRAL"

    def _num(key):                               # price level or None
        v = d.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "verdict": verdict,
        "confidence": int(float(d.get("confidence", 0) or 0)),
        "entry_quality": str(d.get("entry_quality", "")).upper(),
        "suggested_tp": _num("suggested_tp"),    # LOGGED suggestion, never executed
        "suggested_sl": _num("suggested_sl"),
        "suggested_action": str(d.get("suggested_action", "")).upper(),
        "macro": str(d.get("macro", "")),
        "micro": str(d.get("micro", "")),
        "event_risk": str(d.get("event_risk", "")),
        "agree_with_brain": d.get("agree_with_brain"),
        "note": str(d.get("note", "")),
    }


def annotate(images, symbol, direction, entry_price, *, client, system, model,
             max_tokens) -> dict:
    """Send chart image(s) + entry context to Claude; return a verdict dict.

    Never raises — on any failure returns a verdict=ERROR row so the journal still
    records that an entry happened (with the failure reason)."""
    try:
        content = []
        for label, png in images:
            content.append({"type": "text", "text": f"Chart timeframe {label}:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.standard_b64encode(png).decode("utf-8")}})
        if not images:
            content.append({"type": "text", "text": "(chart capture unavailable this cycle)"})
        content.append({"type": "text", "text": (
            "The brain has ALREADY opened this position (final, not yours to change). "
            "Annotate it with macro/micro context.\n"
            f"- Instrument: {symbol}\n"
            f"- Direction taken by the brain: {direction}\n"
            f"- Entry price: {entry_price}\n"
            "Return STRICT JSON only, per the schema.")})
        resp = client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": content}])
        raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        return _parse(raw)
    except Exception as e:                       # fail-safe: advisor must never crash
        logger.exception(f"[advisor:{symbol}] annotate failed")
        return {"verdict": "ERROR", "confidence": 0, "entry_quality": "",
                "suggested_tp": None, "suggested_sl": None, "suggested_action": "",
                "macro": "", "micro": "", "event_risk": "", "agree_with_brain": None,
                "note": f"annotate error: {e}"}


# ----------------------------------------------------------------- the process
class ShadowAdvisor:
    def __init__(self, cfg: dict):
        a = cfg.get("advisor", {}) or {}
        self.poll = int(a.get("poll_seconds", 15))
        self.model = a.get("model", "claude-opus-4-8")
        self.max_tokens = int(a.get("max_tokens", 700))
        self.timeframes = list(a.get("timeframes", ["H4", "H1", "M15"]))
        self.journal = Path(a.get("journal_path", "advisor_journal.jsonl"))
        self.archive = Path(a.get("archive_dir", "_DOC/advisor"))
        self.system = Path(a.get("prompt_file", "pipeline/vision/prompt_advisor.md")
                           ).read_text(encoding="utf-8").strip()
        # magic -> {symbol, tv_symbol}
        self.watch = {int(w["magic"]): {"symbol": w["symbol"], "tv": w["tv_symbol"]}
                      for w in a.get("watch", [])}
        self.seen: set[int] = set()
        self.seeded = False
        self._client = None
        load_dotenv()                            # ANTHROPIC_API_KEY from .env

    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()
        return self._client

    def _record(self, row: dict, images) -> None:
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"[advisor] journal write failed: {e}")
        if images:
            try:
                self.archive.mkdir(parents=True, exist_ok=True)
                stamp = row["ts"].replace(":", "-")
                for label, png in images:
                    (self.archive / f"{stamp}_{row['symbol']}_{label}.png").write_bytes(png)
            except Exception as e:
                logger.warning(f"[advisor] chart archive failed: {e}")

    def _handle(self, pos) -> None:
        w = self.watch[pos.magic]
        direction = "LONG" if pos.type == 0 else "SHORT"
        logger.info(f"[advisor] NEW entry ticket={pos.ticket} {w['symbol']} {direction} "
                    f"@ {pos.price_open} vol={pos.volume} magic={pos.magic} -> annotating")
        try:
            images = capture_multi_tv(w["tv"], self.timeframes)
        except Exception as e:
            logger.warning(f"[advisor] capture failed for {w['symbol']}: {e}")
            images = []
        v = annotate(images, w["symbol"], direction, pos.price_open,
                     client=self._get_client(), system=self.system,
                     model=self.model, max_tokens=self.max_tokens)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "ticket": int(pos.ticket),
            "symbol": w["symbol"],
            "magic": int(pos.magic),
            "direction": direction,
            "entry_price": float(pos.price_open),
            "volume": float(pos.volume),
            "open_time": datetime.fromtimestamp(pos.time, timezone.utc).isoformat(),
            "charts": [lbl for lbl, _ in images],
            **v,
        }
        self._record(row, images)
        logger.info(f"[advisor] ticket={pos.ticket} verdict={v['verdict']} "
                    f"conf={v['confidence']} :: {v['note']}")

    def poll_once(self, mt5) -> None:
        poss = mt5.positions_get()
        if poss is None:                         # MT5 not ready -> skip this poll
            return
        watched = [p for p in poss if p.magic in self.watch]
        if not self.seeded:                      # seed: skip everything already open
            self.seen = {int(p.ticket) for p in watched}
            self.seeded = True
            logger.info(f"[advisor] seeded {len(self.seen)} open ticket(s); "
                        f"will annotate entries opened from now on")
            return
        for p in watched:
            if int(p.ticket) not in self.seen:
                self.seen.add(int(p.ticket))
                self._handle(p)

    def run(self) -> None:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            logger.error(f"[advisor] MT5 initialize() failed: {mt5.last_error()}")
            return
        logger.info(f"[advisor] up. watching magics {sorted(self.watch)} "
                    f"every {self.poll}s, tf={self.timeframes}, model={self.model}")
        try:
            while True:
                try:
                    self.poll_once(mt5)
                except Exception:
                    logger.exception("[advisor] poll error (continuing)")
                time.sleep(self.poll)
        finally:
            mt5.shutdown()


def main() -> None:
    cfg = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    a = cfg.get("advisor", {}) or {}
    if not a.get("enabled", False):
        logger.info("[advisor] disabled in config (advisor.enabled=false). Exiting.")
        return
    ShadowAdvisor(cfg).run()


if __name__ == "__main__":
    main()
