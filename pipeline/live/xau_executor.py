"""XAU executor — pengganti EA MQL5 untuk slot berbasis brain, dijalankan dari Python.

KENAPA ADA: SignalExecutor.mq5 hanya bisa dipasang lewat GUI MT5, dan itu rapuh —
  * Inputs mudah salah (2026-08-10: EA di chart XAUUSD masih memakai ServerSymbol=NAS100
    bawaan, jadi ia rajin heartbeat tapi menanyakan simbol yang salah dan tak pernah
    menerima sinyal XAU sama sekali).
  * Profil chart tidak tersimpan -> restart MT5 menghilangkan chart beserta EA-nya,
    dan SEMUA monitor tetap hijau (insiden 2026-07-05, commit adb721a).
  * Tidak bisa dipasang/diperbaiki tanpa tangan manusia di depan GUI.

Modul ini melakukan persis apa yang dilakukan EA, tapi lewat MT5 Python API:
  poll GET /signals?symbol=... -> untuk tiap sinyal, rekonsiliasi posisi ke `action`.
Pola yang sama sudah terbukti di pipeline/live/orb_stop_manager.py.

KESETIAAN pada SignalExecutor.mq5 (supaya perilakunya identik):
  * `action` adalah DESIRED STATE (BUY/SELL/FLAT), bukan perintah.
  * `signal_id` stabil per sinyal; kita bertindak HANYA saat signal_id berubah per magic.
    Jadi polling cepat tidak pernah membuka order dobel, dan setelah broker menutup di
    SL/TP kita TIDAK membuka ulang sampai ada sinyal baru — sama seperti EA.
  * Satu posisi per magic. Balik arah = tutup lalu buka (stop-and-reverse).
  * SL/TP dipasang di broker, jadi exit tetap jalan walau proses ini mati.

TIDAK menyentuh magic milik manager lain (orb_stop_manager 920617) — modul ini hanya
mengurus magic yang muncul di /signals untuk simbol yang dipantau.

Jalankan:  python -m pipeline.live.xau_executor
"""
from __future__ import annotations

import time

import requests
from loguru import logger

from ..fetch.base_fetcher import load_config

POLL_SECONDS = 2
MAX_LOT = 1.0          # klem pengaman, sama semangatnya dengan input MaxLot di EA
DEVIATION = 20         # poin


class XauExecutor:
    def __init__(self, cfg: dict, symbols: list[str]):
        self.cfg = cfg
        self.symbols = symbols
        live = cfg["live"]
        self.url = f"http://{live.get('host','127.0.0.1')}:{live.get('port',8000)}"
        self.last_id: dict[int, str] = {}      # magic -> signal_id terakhir yang DITINDAKLANJUTI
        self.mt5_sym = {s: cfg["symbols"][s]["mt5_symbol"] for s in symbols}

    # ---------------- MT5 helpers ----------------
    @staticmethod
    def _filling(mt5, info):
        fm = info.filling_mode
        if fm & 2:
            return mt5.ORDER_FILLING_IOC
        if fm & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _position(self, mt5, mt5_symbol: str, magic: int):
        for p in (mt5.positions_get(symbol=mt5_symbol) or []):
            if p.magic == magic:
                return p
        return None

    def _close(self, mt5, pos) -> bool:
        info = mt5.symbol_info(pos.symbol)
        tick = mt5.symbol_info_tick(pos.symbol)
        if info is None or tick is None:
            return False
        is_buy = pos.type == mt5.POSITION_TYPE_BUY
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "position": pos.ticket,
            "symbol": pos.symbol, "volume": pos.volume,
            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
            "price": tick.bid if is_buy else tick.ask,
            "deviation": DEVIATION, "magic": pos.magic,
            "type_filling": self._filling(mt5, info), "comment": "xauexec_close",
        }
        r = mt5.order_send(req)
        ok = bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)
        logger.info(f"[xauexec] close magic={pos.magic} ticket={pos.ticket} -> "
                    f"{'OK' if ok else 'GAGAL ' + str(getattr(r, 'retcode', None))}")
        return ok

    def _open(self, mt5, mt5_symbol: str, sig) -> bool:
        info = mt5.symbol_info(mt5_symbol)
        tick = mt5.symbol_info_tick(mt5_symbol)
        if info is None or tick is None:
            return False
        if not info.visible:
            mt5.symbol_select(mt5_symbol, True)
        lot = max(info.volume_min, min(float(sig["lot"]), MAX_LOT))
        step = info.volume_step or 0.01
        lot = round(round(lot / step) * step, 2)
        is_buy = sig["action"] == "BUY"
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": mt5_symbol, "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": tick.ask if is_buy else tick.bid,
            "sl": round(float(sig["sl"]), info.digits) if sig["sl"] else 0.0,
            "tp": round(float(sig["tp"]), info.digits) if sig["tp"] else 0.0,
            "deviation": DEVIATION, "magic": int(sig["magic"]),
            "type_filling": self._filling(mt5, info),
            "comment": str(sig["strategy"])[:31],
        }
        r = mt5.order_send(req)
        ok = bool(r and r.retcode == mt5.TRADE_RETCODE_DONE)
        logger.info(f"[xauexec] {sig['action']} {sig['strategy']} magic={sig['magic']} "
                    f"lot={lot} sl={req['sl']} tp={req['tp']} -> "
                    f"{'OK' if ok else 'GAGAL ' + str(getattr(r, 'retcode', None))}")
        return ok

    # ---------------- inti ----------------
    def reconcile(self, mt5, mt5_symbol: str, sig) -> None:
        """Bawa posisi ke keadaan yang diminta `action`. Persis ReconcileTo() di EA."""
        magic = int(sig["magic"])
        want = sig["action"]
        pos = self._position(mt5, mt5_symbol, magic)
        cur = None
        if pos is not None:
            cur = "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL"
        if want == "FLAT":
            if pos is not None:
                self._close(mt5, pos)
            return
        if cur == want:
            return                                   # sudah sesuai
        if pos is not None:
            if not self._close(mt5, pos):            # balik arah: tutup dulu
                return
        self._open(mt5, mt5_symbol, sig)

    def poll_once(self, mt5) -> None:
        for sym in self.symbols:
            try:
                r = requests.get(f"{self.url}/signals", params={"symbol": sym}, timeout=20)
                r.raise_for_status()
                signals = r.json().get("signals", [])
            except Exception as e:
                logger.warning(f"[xauexec] gagal ambil sinyal {sym}: {e}")
                continue
            mt5_symbol = self.mt5_sym[sym]
            for sig in signals:
                magic = int(sig["magic"])
                sid = sig["signal_id"]
                if self.last_id.get(magic) == sid:
                    continue                          # sinyal ini SUDAH ditindaklanjuti
                self.reconcile(mt5, mt5_symbol, sig)
                self.last_id[magic] = sid

    def run(self) -> None:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            logger.error(f"[xauexec] MT5 initialize gagal: {mt5.last_error()}")
            return
        logger.info(f"[xauexec] up. symbols={self.symbols} poll={POLL_SECONDS}s url={self.url}")
        # Adopsi keadaan awal: catat signal_id yang berlaku SEKARANG tanpa bertindak,
        # supaya start ulang tidak langsung membuka posisi dari sinyal lama.
        try:
            for sym in self.symbols:
                r = requests.get(f"{self.url}/signals", params={"symbol": sym}, timeout=20)
                for sig in r.json().get("signals", []):
                    magic = int(sig["magic"])
                    pos = self._position(mt5, self.mt5_sym[sym], magic)
                    if (pos is None) == (sig["action"] == "FLAT"):
                        self.last_id[magic] = sig["signal_id"]   # sudah sinkron -> jangan ulangi
                        logger.info(f"[xauexec] adopsi magic={magic} action={sig['action']}")
        except Exception as e:
            logger.warning(f"[xauexec] adopsi awal dilewati: {e}")

        while True:
            try:
                self.poll_once(mt5)
            except Exception as e:
                logger.exception(f"[xauexec] poll error (loop tetap jalan): {e}")
            time.sleep(POLL_SECONDS)


def main() -> None:
    cfg = load_config()
    # simbol yang punya slot brain aktif; magic milik manager lain tidak akan muncul
    # di /signals karena slotnya enabled:false (orb30_nas dimiliki orb_stop_manager).
    syms = sorted({s["symbol"] for s in cfg["live"]["strategies"] if s.get("enabled", True)})
    if not syms:
        logger.info("[xauexec] tidak ada slot aktif. Keluar.")
        return
    XauExecutor(cfg, syms).run()


if __name__ == "__main__":
    main()
