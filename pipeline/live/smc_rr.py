"""Agent RR untuk sleeve SMC — menilai SL / TP / time-exit tiap zona sebelum order dipasang.

Dipanggil SINKRON oleh smc_limit_manager tepat sebelum order dikirim. Agent menerima
zona Order Block yang sudah dihitung mesin (deterministik) lalu boleh MENYESUAIKAN
SL, TP, dan masa berlaku — atau menyarankan LEWATI.

RANCANGAN YANG SENGAJA DIPILIH: agent adalah PENYESUAI, bukan penentu tunggal.
Mesin selalu menghasilkan angka deterministik lebih dulu; agent menggeser angka itu
di dalam batas yang keras. Alasannya dua, keduanya praktis:

  1. Kredit API bisa habis, jaringan bisa putus, model bisa mengembalikan sampah.
     Kalau agent WAJIB, satu kegagalan API = sleeve berhenti total tanpa order.
     Dengan rancangan ini, kegagalan apa pun -> pakai angka mesin, trading jalan terus.
  2. Keluaran model tidak deterministik. Batas keras membuat kerusakan paling parah
     yang bisa ia sebabkan tetap terbatas dan bisa diaudit.

BATAS KERAS (agent TIDAK bisa melewatinya — dilanggar = angka mesin yang dipakai):
  * SL tidak boleh LEBIH JAUH dari `sl_max_mult` x jarak SL mesin  -> membatasi risiko
  * SL tidak boleh lebih dekat dari `min_sl_usd`                    -> cegah SL tercekik
  * RR hasil akhir tidak boleh di bawah `rr_min`                    -> cegah TP dipepet
  * expiry harus di rentang [`expiry_min_bars`, `expiry_max_bars`]
  * arah TIDAK PERNAH bisa diubah agent; sisi SL/TP harus tetap benar

Semua keputusan (dipakai / ditolak / fallback) dicatat ke `smc_rr_journal.jsonl`
berikut angka mesin DAN angka agent, supaya belakangan bisa diukur: apakah
penyesuaian agent benar-benar memperbaiki hasil, atau justru merusaknya.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parents[2]

SKEMA = """You adjust risk parameters for an already-decided trade. Return STRICT JSON only:
{
  "action": "TAKE" | "SKIP",
  "sl": <price or null>,
  "tp": <price or null>,
  "expiry_bars": <int or null>,
  "confidence": <0-100>,
  "event_risk": "<scheduled high-impact events before expiry, or 'none'>",
  "reason": "<one or two sentences>"
}
null means "keep the machine value". Never change the trade direction."""


class RrAgent:
    def __init__(self, cfg: dict):
        a = (cfg.get("smc_rr") or {})
        self.enabled = bool(a.get("enabled", False))
        self.model = a.get("model", "claude-opus-5")
        self.max_tokens = int(a.get("max_tokens", 1200))
        self.timeout = float(a.get("timeout_seconds", 90))
        self.web_search = bool(a.get("web_search", True))
        self.timeframes = list(a.get("timeframes", ["H4", "H1"]))
        self.tv_symbol = a.get("tv_symbol", "OANDA:XAUUSD")
        self.allow_skip = bool(a.get("allow_skip", False))
        # batas keras
        self.sl_max_mult = float(a.get("sl_max_mult", 1.5))
        self.min_sl_usd = float(a.get("min_sl_usd", 3.0))
        self.rr_min = float(a.get("rr_min", 1.2))
        self.expiry_min = int(a.get("expiry_min_bars", 3))
        self.expiry_max = int(a.get("expiry_max_bars", 24))
        self.journal = ROOT / a.get("journal_path", "smc_rr_journal.jsonl")
        self._client = None

    # ---------------------------------------------------------------- util
    def _catat(self, row: dict) -> None:
        try:
            with self.journal.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception as e:
            logger.warning(f"[smcrr] gagal menulis jurnal: {e}")

    def _client_or_none(self):
        if self._client is None:
            try:
                from dotenv import load_dotenv
                import anthropic
                load_dotenv()
                if not os.getenv("ANTHROPIC_API_KEY"):
                    return None
                self._client = anthropic.Anthropic(timeout=self.timeout)
            except Exception:
                logger.exception("[smcrr] gagal membuat client")
                return None
        return self._client

    @staticmethod
    def _json_pertama(teks: str) -> dict:
        import re
        s = (teks or "").strip()
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL)
        if m:
            return json.loads(m.group(1))
        m2 = re.search(r"\{.*\}", s, re.DOTALL)
        if not m2:
            raise ValueError("tidak ada JSON di balasan")
        return json.loads(m2.group(0))

    # ---------------------------------------------------------------- batas
    def _sahkan(self, arah: int, px: float, mesin: dict, usul: dict) -> tuple[dict, list[str]]:
        """Terapkan batas keras. Kembalikan (nilai_terpakai, daftar_alasan_penolakan)."""
        pakai = dict(mesin)
        tolak: list[str] = []
        sl_mesin_dist = abs(px - mesin["sl"])

        sl = usul.get("sl")
        if sl is not None:
            sl = float(sl)
            d = abs(px - sl)
            sisi_benar = (sl < px) if arah == 1 else (sl > px)
            if not sisi_benar:
                tolak.append(f"SL {sl} di sisi salah")
            elif d > self.sl_max_mult * sl_mesin_dist:
                tolak.append(f"SL {d:.2f} > {self.sl_max_mult}x mesin {sl_mesin_dist:.2f}")
            elif d < self.min_sl_usd:
                tolak.append(f"SL {d:.2f} < minimum ${self.min_sl_usd}")
            else:
                pakai["sl"] = sl

        tp = usul.get("tp")
        if tp is not None:
            tp = float(tp)
            sisi_benar = (tp > px) if arah == 1 else (tp < px)
            rr = abs(tp - px) / max(1e-9, abs(px - pakai["sl"]))
            if not sisi_benar:
                tolak.append(f"TP {tp} di sisi salah")
            elif rr < self.rr_min:
                tolak.append(f"RR {rr:.2f} < minimum {self.rr_min}")
            else:
                pakai["tp"] = tp

        eb = usul.get("expiry_bars")
        if eb is not None:
            eb = int(eb)
            if not (self.expiry_min <= eb <= self.expiry_max):
                tolak.append(f"expiry {eb} di luar [{self.expiry_min},{self.expiry_max}]")
            else:
                pakai["expiry_bars"] = eb

        # RR akhir harus tetap sah walau hanya SL yang berubah
        rr_akhir = abs(pakai["tp"] - px) / max(1e-9, abs(px - pakai["sl"]))
        if rr_akhir < self.rr_min:
            tolak.append(f"RR akhir {rr_akhir:.2f} < {self.rr_min} -> kembali ke mesin")
            pakai = dict(mesin)
        return pakai, tolak

    # ---------------------------------------------------------------- utama
    def nilai(self, *, arah: int, price: float, mesin: dict, symbol: str,
              expiry_utc, tick_bid: float) -> dict:
        """Kembalikan {sl, tp, expiry_bars, sumber, skip, ...}. TIDAK PERNAH melempar."""
        hasil = dict(mesin); hasil.update({"sumber": "mesin", "skip": False,
                                           "verdict": None, "reason": ""})
        if not self.enabled:
            return hasil

        client = self._client_or_none()
        if client is None:
            logger.warning("[smcrr] tidak ada API key/client -> pakai angka mesin")
            self._catat({"ts": datetime.now(timezone.utc).isoformat(), "symbol": symbol,
                         "arah": arah, "price": price, "mesin": mesin,
                         "sumber": "mesin", "error": "client tidak tersedia"})
            return hasil

        images = []
        try:
            from pipeline.vision.tv_capture import capture_multi_tv
            images = capture_multi_tv(self.tv_symbol, self.timeframes)
        except Exception as e:
            logger.warning(f"[smcrr] capture chart gagal: {e}")

        import base64
        content = []
        for label, png in images:
            content.append({"type": "text", "text": f"Chart timeframe {label}:"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/png",
                "data": base64.standard_b64encode(png).decode("utf-8")}})
        arah_txt = "LONG" if arah == 1 else "SHORT"
        content.append({"type": "text", "text": (
            f"A Smart-Money-Concepts Order Block zone has been identified on {symbol} H4 "
            f"and a resting {arah_txt} LIMIT order is about to be placed.\n\n"
            f"The DIRECTION and the ENTRY price are already fixed and are NOT yours to "
            f"change. Your only job is to sanity-check the risk parameters.\n\n"
            f"- Entry (Order Block edge): {price}\n"
            f"- Current market bid: {tick_bid}\n"
            f"- Machine stop loss: {mesin['sl']}  (distance ${abs(price - mesin['sl']):.2f})\n"
            f"- Machine take profit: {mesin['tp']}  (RR "
            f"{abs(mesin['tp'] - price) / max(1e-9, abs(price - mesin['sl'])):.2f})\n"
            f"- Machine time exit: {mesin['expiry_bars']} H4 bars "
            f"(order expires {expiry_utc:%Y-%m-%d %H:%M} UTC if unfilled)\n\n"
            "Consider structure, nearby liquidity, and scheduled event risk before the "
            "expiry. Use web search to check the economic calendar and gold headlines.\n"
            "Return null for any field you would leave unchanged.\n" + SKEMA)})

        kw = {}
        if self.web_search:
            kw["tools"] = [{"type": "web_search_20260209", "name": "web_search",
                            "max_uses": 4}]
        try:
            msgs = [{"role": "user", "content": content}]
            resp = client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                          messages=msgs, **kw)
            for _ in range(2):
                if getattr(resp, "stop_reason", None) != "pause_turn":
                    break
                msgs = msgs + [{"role": "assistant", "content": resp.content}]
                resp = client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                              messages=msgs, **kw)
            texts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
            usul = None
            for t in reversed(texts):
                try:
                    usul = self._json_pertama(t); break
                except Exception:
                    continue
            if usul is None:
                raise ValueError("balasan tanpa JSON yang bisa dibaca")
        except Exception as e:
            logger.warning(f"[smcrr] panggilan agent GAGAL ({e}) -> pakai angka mesin")
            self._catat({"ts": datetime.now(timezone.utc).isoformat(), "symbol": symbol,
                         "arah": arah, "price": price, "mesin": mesin,
                         "sumber": "mesin", "error": str(e)[:400]})
            return hasil

        pakai, tolak = self._sahkan(arah, price, mesin, usul)
        aksi = str(usul.get("action", "TAKE")).upper()
        skip = (aksi == "SKIP") and self.allow_skip
        berubah = any(pakai[k] != mesin[k] for k in ("sl", "tp", "expiry_bars"))
        pakai.update({"sumber": "agent" if berubah else "mesin", "skip": skip,
                      "verdict": aksi, "reason": str(usul.get("reason", ""))[:500]})

        self._catat({"ts": datetime.now(timezone.utc).isoformat(), "symbol": symbol,
                     "arah": arah, "price": price, "mesin": mesin, "usul": usul,
                     "terpakai": {k: pakai[k] for k in ("sl", "tp", "expiry_bars")},
                     "ditolak": tolak, "sumber": pakai["sumber"], "skip": skip,
                     "verdict": aksi, "event_risk": str(usul.get("event_risk", ""))[:400],
                     "confidence": usul.get("confidence"),
                     "reason": pakai["reason"], "charts": [l for l, _ in images]})
        if tolak:
            logger.warning(f"[smcrr] usul agent DITOLAK sebagian: {'; '.join(tolak)}")
        logger.info(f"[smcrr] {aksi} sumber={pakai['sumber']} sl={pakai['sl']} "
                    f"tp={pakai['tp']} expiry={pakai['expiry_bars']} :: {pakai['reason'][:120]}")
        return pakai
