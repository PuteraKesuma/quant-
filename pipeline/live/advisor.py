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
             max_tokens, task_text: str | None = None, web_search: bool = False) -> dict:
    """Send chart image(s) + entry context to Claude; return a verdict dict.

    `task_text` overrides the default "position already opened" framing — used by the
    SMC zone trigger, which fires when a LIMIT is ARMED (before any fill).

    `web_search` arms Anthropic's server-side search tool so the model reads the live
    economic calendar and gold headlines itself instead of relying on stale context.
    That is the whole point of the zone trigger: at ~20 alerts/year the call is cheap,
    so it can afford to actually go and look.

    Never raises — on any failure returns a verdict=ERROR row so the journal still
    records that the event happened (with the failure reason)."""
    try:
        content = []
        for label, png in images:
            content.append({"type": "text", "text": f"Chart timeframe {label}:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.standard_b64encode(png).decode("utf-8")}})
        if not images:
            content.append({"type": "text", "text": "(chart capture unavailable this cycle)"})
        content.append({"type": "text", "text": task_text or (
            "The brain has ALREADY opened this position (final, not yours to change). "
            "Annotate it with macro/micro context.\n"
            f"- Instrument: {symbol}\n"
            f"- Direction taken by the brain: {direction}\n"
            f"- Entry price: {entry_price}\n"
            "Return STRICT JSON only, per the schema.")})

        kw = {}
        if web_search:
            # max_uses bounds the cost; the model still decides whether to search.
            kw["tools"] = [{"type": "web_search_20260209", "name": "web_search",
                            "max_uses": 4}]

        messages = [{"role": "user", "content": content}]
        resp = client.messages.create(model=model, max_tokens=max_tokens,
                                      system=system, messages=messages, **kw)
        # A server-tool turn can stop with pause_turn; resend once to let it finish.
        for _ in range(2):
            if getattr(resp, "stop_reason", None) != "pause_turn":
                break
            messages = messages + [{"role": "assistant", "content": resp.content}]
            resp = client.messages.create(model=model, max_tokens=max_tokens,
                                          system=system, messages=messages, **kw)

        # Prefer the LAST text block: with web_search the reply also carries search
        # narration, and a greedy {...} match over the whole thing can span past the JSON.
        texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        for t in reversed(texts):
            try:
                return _parse(t)
            except Exception:
                continue
        return _parse("".join(texts))
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
        # Magic yang diberi verdict saat ZONA TER-ARM (pending LIMIT dipasang), bukan
        # saat terisi. Dipakai SMC: pemicunya alert zona, bukan posisi.
        #   - verdict datang SEBELUM fill, jadi ada gunanya untuk dinilai belakangan
        #   - order yang KEDALUWARSA tanpa terisi juga dapat label (16 dari 31 pending
        #     di jendela uji) -> dataset berlabelnya dua kali lipat
        self.watch_pending = {int(m) for m in a.get("watch_pending", [])}
        self.web_search = bool(a.get("web_search", False))
        self.seen: set[int] = set()
        self.seen_pending: set[int] = set()
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

    def _handle_pending(self, o) -> None:
        """Verdict saat ZONA TER-ARM: order LIMIT dipasang, belum terisi."""
        w = self.watch[o.magic]
        arah = "LONG" if o.type in (2, 4, 6) else "SHORT"    # *_LIMIT / *_STOP buy = genap
        exp = (datetime.fromtimestamp(o.time_expiration, timezone.utc).isoformat()
               if getattr(o, "time_expiration", 0) else None)
        logger.info(f"[advisor] ZONA TER-ARM order={o.ticket} {w['symbol']} {arah} "
                    f"limit={o.price_open} sl={o.sl} tp={o.tp} exp={exp} -> annotating")
        try:
            images = capture_multi_tv(w["tv"], self.timeframes)
        except Exception as e:
            logger.warning(f"[advisor] capture failed for {w['symbol']}: {e}")
            images = []
        tugas = (
            "A resting LIMIT order has just been ARMED at a Smart-Money-Concepts Order "
            "Block zone. It is NOT filled yet and may expire unfilled. You are NOT "
            "deciding anything — the order is already placed and will not be changed. "
            "Your job is to record the market context so it can be scored later.\n"
            f"- Instrument: {w['symbol']}\n"
            f"- Direction if filled: {arah}\n"
            f"- Limit (Order Block edge): {o.price_open}\n"
            f"- Stop loss: {o.sl}    Take profit: {o.tp}\n"
            f"- Order EXPIRES at: {exp} (UTC). It fills only if price reaches the zone "
            "before then.\n\n"
            "Use web search to check, for the window between now and that expiry:\n"
            "  1. scheduled high-impact USD / gold events (CPI, NFP, FOMC, PCE)\n"
            "  2. current gold headlines and positioning sentiment\n"
            "Put the concrete findings in `event_risk` (name the events and their dates/"
            "times, or say explicitly that none are scheduled) and `macro`.\n"
            "Return STRICT JSON only, per the schema.")
        v = annotate(images, w["symbol"], arah, float(o.price_open),
                     client=self._get_client(), system=self.system,
                     model=self.model, max_tokens=self.max_tokens,
                     task_text=tugas, web_search=self.web_search)
        row = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "zone_armed",               # bedakan dari baris entry biasa
            "order_ticket": int(o.ticket),
            "symbol": w["symbol"],
            "magic": int(o.magic),
            "direction": arah,
            "limit_price": float(o.price_open),
            "sl": float(o.sl), "tp": float(o.tp),
            "expires_utc": exp,
            "charts": [lbl for lbl, _ in images],
            **v,
        }
        self._record(row, images)
        logger.info(f"[advisor] order={o.ticket} verdict={v['verdict']} "
                    f"conf={v['confidence']} :: {v['note']}")

    def poll_once(self, mt5) -> None:
        poss = mt5.positions_get()
        if poss is None:                         # MT5 not ready -> skip this poll
            return
        pends = mt5.orders_get() if self.watch_pending else []
        pends = [] if pends is None else [o for o in pends
                                          if o.magic in self.watch_pending
                                          and o.magic in self.watch]
        watched = [p for p in poss if p.magic in self.watch]
        if not self.seeded:                      # seed: skip everything already open
            self.seen = {int(p.ticket) for p in watched}
            self.seen_pending = {int(o.ticket) for o in pends}
            self.seeded = True
            logger.info(f"[advisor] seeded {len(self.seen)} open ticket(s) and "
                        f"{len(self.seen_pending)} pending; will annotate from now on")
            return
        for o in pends:                          # zona ter-arm (SMC) — sebelum fill
            if int(o.ticket) not in self.seen_pending:
                self.seen_pending.add(int(o.ticket))
                try:
                    self._handle_pending(o)
                except Exception:
                    logger.exception("[advisor] pending handler failed (continuing)")
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
