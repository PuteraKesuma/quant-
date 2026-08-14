"""SMC limit manager — pending BUY/SELL LIMIT di zona Order Block, DENGAN EXPIRY.

Memasang order pending sungguhan lewat MT5 Python API (EA tidak disentuh), pola sama
persis dengan liquidity_manager.py. Bedanya: order SMC punya masa berlaku terbatas
(`ORDER_TIME_SPECIFIED`) dan TIDAK pernah di-reprice — zona Order Block ditetapkan
sekali saat BOS terjadi, dan itulah inti konsepnya.

  BOS bullish -> BUY_LIMIT  di ujung ATAS zona OB (harga harus retrace turun ke zona)
  BOS bearish -> SELL_LIMIT di ujung BAWAH zona OB
  SL di luar zona + buffer, TP = rr x jarak SL, kedaluwarsa setelah `expiry_bars` bar.

DASAR RISET: research/smc_xau_backtest.py konfigurasi H4-B (OB + BOS + FVG).
  n=96  net +$630.93  PF 1.81  maxDD -14.8%  5/6 tahun hijau  margin impas +13.5 poin
  dataran parameter: 21/21 varian untung, acuan BUKAN puncak di sumbu mana pun
  risk-adjusted mengalahkan beli-dan-tahan (42.5 vs 11.9 net per poin DD) dan setiap
  pembanding bodoh tanpa OB/FVG (42.5 vs 14.5 / 9.9 / 24.3)

  YANG HARUS DISADARI OPERATOR — ini dipasang meski TIDAK lolos ambang:
  * DSR 0.629, ambang 0.95, dengan 16 trial dilaporkan jujur.
  * 2021-2023: 52 trade, net -$9.54. TIGA TAHUN MENGHASILKAN NOL. Kalau rezim emas
    kembali datar seperti itu, slot ini bisa diam bertahun-tahun. Itu BUKAN kerusakan.
  * Buang 5 trade terbaik dari 96 -> net -$46. Hasilnya bertumpu pada segelintir trade,
    jadi slot ini HARUS dibiarkan jalan; melewatkan trade besar menghapus segalanya.
  * Semua pembanding bodoh punya belah waktu yang sama -> KAPAN untungnya ditentukan
    rezim emas, bukan oleh SMC. Yang ditambahkan SMC adalah kualitas per satuan risiko.
  * ~17 trade/tahun. Jangan cemas kalau berminggu-minggu tidak ada order.

KESETIAAN KE BACKTEST: fungsi `_setup_terkini()` MENJALANKAN ULANG penelusuran bar yang
sama persis seperti backtest (BOS sebagai PERISTIWA sekali-pakai per level, pivot baru
dipakai setelah k bar konfirmasi), lalu mengambil keadaan di bar terakhir. Itu disengaja:
menulis ulang logikanya dalam bentuk "cek kondisi sekarang" adalah cara paling umum
live menyimpang dari backtest (pelajaran RSI2: 4 cacat paritas, -81%).
Paritasnya diuji oleh research/smc_paritas.py.

SAFETY: `dry_run` (default true) mencatat semua place/cancel TANPA mengirim.
Run:  python -m pipeline.live.smc_limit_manager
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from ..fetch.base_fetcher import load_config
from .data import DataProvider

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "_MONITOR" / "smc_state.json"
GOV = ROOT / "_MONITOR" / "governor.json"


def _governor_menjeda() -> bool:
    """True kalau governor sedang menjeda entry baru.

    Ditambahkan 2026-08-14: sebelumnya manager ini SAMA SEKALI tidak mengenal
    governor (0 rujukan), jadi SMC akan tetap membuka posisi meski rem harian atau
    rem kerugian maksimum sudah aktif. Sengaja FAIL-OPEN kalau file tidak terbaca:
    governor mati tidak boleh mematikan trading, hanya jeda EKSPLISIT yang boleh.
    """
    try:
        return bool(json.loads(GOV.read_text(encoding="utf-8")).get("paused", False))
    except Exception:
        return False


class SmcLimitManager:
    def __init__(self, cfg: dict, spec: dict):
        p = spec.get("params", {})
        self.cfg = cfg
        self.symbol = spec["symbol"]
        self.mt5_symbol = cfg["symbols"][self.symbol]["mt5_symbol"]
        self.magic = int(spec["magic"])
        self.lot = float(spec["lot"])
        self.timeframe = str(p.get("timeframe", "4h"))
        self.k = int(p.get("swing_k", 3))
        self.ob_lookback = int(p.get("ob_lookback", 10))
        self.expiry_bars = int(p.get("expiry_bars", 12))
        self.rr = float(p.get("rr", 2.0))
        self.buffer_frac = float(p.get("buffer_frac", 0.10))
        self.use_fvg = bool(p.get("use_fvg", True))
        self.use_sweep = bool(p.get("use_sweep", False))
        self.sweep_window = int(p.get("sweep_window", 5))
        # Mode eksekusi:
        #   "limit"      -> pending LIMIT menganggur di ujung zona, broker mengisi otomatis
        #   "m5_confirm" -> TIDAK ada pending. Tunggu harga menyentuh zona, lalu wajib ada
        #                   BOS M5 searah dalam `konfirm_bars_m5` bar, baru kirim MARKET.
        # Diukur di research/smc_konfirmasi_m5.py: konfirmasi M5 MENOLONG H1-C
        # (net/DD 153->291, WR 50%->68%, maxDD -7.8%->-4.4%) tapi MERUSAK H4-B
        # (net/DD 98->51). Karena itu mode-nya per-slot, bukan global.
        self.entry_mode = str(p.get("entry_mode", "limit"))
        self.konfirm_bars_m5 = int(p.get("konfirm_bars_m5", 12))
        # Konfirmasi yang sudah lewat lama TIDAK boleh dieksekusi: harganya sudah basi.
        self.konfirm_max_umur = int(p.get("konfirm_max_umur_bar", 2))
        self.history_bars = int(p.get("history_bars", 60000))
        self.poll = int(p.get("manager_poll_seconds", 30))
        self.dry_run = bool(p.get("dry_run", True))
        # Batas setup per hari (permintaan user 2026-08-13). Dihitung per hari UTC dan
        # menghitung ORDER YANG DIPASANG, bukan yang terisi - "maksimal 2 kali set".
        self.max_per_day = int(p.get("max_setups_per_day", 2))
        # --- RISIKO SESUAI BALANCE (permintaan user 2026-08-14) ---
        # lot = (balance x risk_pct) / (jarak_SL x contract), dijepit ke [min, lot_maks]
        # dan dibulatkan ke bawah ke kelipatan volume broker.
        #
        # Diukur di research/smc_sizing.py (equity di-compound dari $523):
        #   H1-C  lot tetap 0.01 -> Calmar 5.86 | risk 2% -> Calmar 10.81
        #   H4-B  lot tetap 0.01 -> Calmar 2.23 | risk 2% -> Calmar 1.68 (LEBIH BURUK,
        #         karena SL-nya jauh lebih lebar: median $9.80 vs $6.02, maks $101 vs $41)
        # Karena itu risk_pct diatur PER SLOT, bukan global.
        #
        # TIDAK ADA plafon yang MELEWATI trade: diuji dan merusak. Plafon 3% di H4-B
        # membuang 26 dari 99 trade dan memangkas Calmar 2.23 -> 0.24. Kalau lot minimum
        # sudah melewati target risiko, trade tetap DIAMBIL di lot minimum - melewatkan
        # trade besar jauh lebih mahal daripada sesekali over-risk pada lot terkecil.
        self.risk_pct = float(p.get("risk_pct", 0.0))     # 0 = pakai lot tetap
        self.lot_maks = float(p.get("lot_maks", 0.05))
        self.contract = float(p.get("contract_size", 100.0))
        self.data = DataProvider(cfg)
        from .smc_rr import RrAgent
        self.rr_agent = RrAgent(cfg)

    # ---------------------------------------------------------------- state
    # State dikunci per MAGIC supaya beberapa aliran SMC (H4-B dan H1-C) bisa hidup
    # berdampingan tanpa saling menimpa hitungan setup harian atau penanda BOS.
    @staticmethod
    def _baca_semua() -> dict:
        try:
            d = json.loads(STATE.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def _baca_state(self) -> dict:
        return self._baca_semua().get(str(self.magic), {})

    def _tulis_state(self, d: dict) -> None:
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            semua = self._baca_semua()
            semua[str(self.magic)] = d
            STATE.write_text(json.dumps(semua, indent=2), encoding="utf-8")
        except Exception:
            logger.exception("[smcmgr] gagal menulis state (lanjut)")

    # ---------------------------------------------------------------- SMC
    @staticmethod
    def _pivots(hi: np.ndarray, lo: np.ndarray, k: int):
        """Fractal +-k. Mengembalikan (idx_pivot, level, idx_konfirmasi=idx+k)."""
        sh, sl = [], []
        for i in range(k, len(hi) - k):
            w_hi = hi[i - k:i + k + 1]
            w_lo = lo[i - k:i + k + 1]
            if hi[i] == w_hi.max() and w_hi.argmax() == k:
                sh.append((i, float(hi[i]), i + k))
            if lo[i] == w_lo.min() and w_lo.argmin() == k:
                sl.append((i, float(lo[i]), i + k))
        return sh, sl

    def _cari_ob(self, o, c, hi, lo, j: int, arah: int):
        awal = max(0, j - self.ob_lookback)
        for i in range(j - 1, awal - 1, -1):
            bearish = c[i] < o[i]
            if (arah == 1 and bearish) or (arah == -1 and not bearish):
                return i, float(lo[i]), float(hi[i])
        return None

    @staticmethod
    def _ada_fvg(hi, lo, i0: int, i1: int, arah: int) -> bool:
        for t in range(max(i0 + 1, 1), min(i1, len(hi) - 1)):
            if arah == 1 and lo[t + 1] > hi[t - 1]:
                return True
            if arah == -1 and hi[t + 1] < lo[t - 1]:
                return True
        return False

    @staticmethod
    def _level_terkonfirmasi(pivots, n: int) -> np.ndarray:
        """Level pivot terakhir yang SUDAH terkonfirmasi di tiap bar (nan kalau belum)."""
        out = np.full(n, np.nan)
        p = 0; cur = np.nan
        for j in range(n):
            while p < len(pivots) and pivots[p][2] <= j:
                cur = pivots[p][1]; p += 1
            out[j] = cur
        return out

    def _ada_sweep(self, hi, lo, c, lvl_lawan: np.ndarray, i_ob: int, arah: int) -> bool:
        """Wick menembus pivot lawan lalu close balik ke dalam (ambil likuiditas stop)."""
        for t in range(max(0, i_ob - self.sweep_window), i_ob + 1):
            ref = lvl_lawan[t]
            if ref != ref:                       # nan
                continue
            if arah == 1 and lo[t] < ref and c[t] > ref:
                return True
            if arah == -1 and hi[t] > ref and c[t] < ref:
                return True
        return False

    def _setup_terkini(self, h: pd.DataFrame):
        """Telusuri bar PERSIS seperti backtest; kembalikan pending yang aktif di bar akhir.

        Mengembalikan dict {arah, price, sl, tp, bos_time, expiry_time} atau None.
        Sengaja menjalankan ulang seluruh penelusuran, bukan memeriksa "kondisi
        sekarang", supaya semantik BOS-sebagai-peristiwa identik dengan backtest.
        """
        o = h["open"].to_numpy(); c = h["close"].to_numpy()
        hi = h["high"].to_numpy(); lo = h["low"].to_numpy()
        idx = h.index
        n = len(h)
        if n < self.k * 2 + self.ob_lookback + 5:
            return None

        sh, sl_piv = self._pivots(hi, lo, self.k)
        lvl_sl = self._level_terkonfirmasi(sl_piv, n) if self.use_sweep else None
        lvl_sh = self._level_terkonfirmasi(sh, n) if self.use_sweep else None
        i_sh = i_sl = 0
        last_sh = last_sl = None
        sh_ditembus = sl_ditembus = False
        pend = None          # (arah, px, sl, tp, exp_bar, bos_bar)
        # Posisi virtual WAJIB disimulasikan lengkap dengan keluar SL/TP. Versi awal
        # fungsi ini melewatkan itu dan uji paritas (research/smc_paritas.py) menangkap
        # 2 "order hantu": manager meng-arm pending saat backtest masih memegang posisi.
        # Live kebetulan aman karena poll_once memeriksa positions_get() lebih dulu, tapi
        # dua jalur kode harus sepakat KARENA KONSTRUKSINYA, bukan karena pengaman lain.
        pos = 0; p_sl = p_tp = 0.0

        for j in range(1, n):
            while i_sh < len(sh) and sh[i_sh][2] <= j:
                last_sh = sh[i_sh][1]; i_sh += 1; sh_ditembus = False
            while i_sl < len(sl_piv) and sl_piv[i_sl][2] <= j:
                last_sl = sl_piv[i_sl][1]; i_sl += 1; sl_ditembus = False

            # posisi terbuka: SL diprioritaskan (konservatif, sama seperti backtest)
            if pos != 0:
                if pos == 1:
                    if lo[j] <= p_sl or hi[j] >= p_tp:
                        pos = 0
                else:
                    if hi[j] >= p_sl or lo[j] <= p_tp:
                        pos = 0

            # pending kedaluwarsa / terisi
            if pend is not None and pos == 0:
                arah, px, s_, t_, exp_bar, _b = pend
                kena = (lo[j] <= px) if arah == 1 else (hi[j] >= px)
                if kena:
                    pos, p_sl, p_tp = arah, s_, t_
                    pend = None
                elif j >= exp_bar:
                    pend = None
            elif pend is not None:
                pend = None

            arah = 0
            if last_sh is not None and not sh_ditembus and c[j] > last_sh:
                arah = 1; sh_ditembus = True
            elif last_sl is not None and not sl_ditembus and c[j] < last_sl:
                arah = -1; sl_ditembus = True
            if arah == 0:
                continue
            if pos != 0 or pend is not None:
                continue

            ob = self._cari_ob(o, c, hi, lo, j, arah)
            if ob is None:
                continue
            i_ob, ob_lo, ob_hi = ob
            if ob_hi <= ob_lo:
                continue
            if self.use_fvg and not self._ada_fvg(hi, lo, i_ob, j, arah):
                continue
            if self.use_sweep:
                lawan = lvl_sl if arah == 1 else lvl_sh
                if not self._ada_sweep(hi, lo, c, lawan, i_ob, arah):
                    continue

            buf = (ob_hi - ob_lo) * self.buffer_frac
            if arah == 1:
                px = ob_hi; s = ob_lo - buf
                if px <= s or px >= c[j]:
                    continue
                t = px + self.rr * (px - s)
            else:
                px = ob_lo; s = ob_hi + buf
                if px >= s or px <= c[j]:
                    continue
                t = px - self.rr * (s - px)
            pend = (arah, px, s, t, j + self.expiry_bars, j)

        if pend is None or pos != 0:
            return None
        arah, px, s, t, exp_bar, bos_bar = pend
        delta = pd.Timedelta(self.timeframe)
        return {"arah": arah, "price": float(px), "sl": float(s), "tp": float(t),
                "bos_time": idx[bos_bar], "expiry_time": idx[bos_bar] + self.expiry_bars * delta}

    def _konfirmasi_m5(self, setup: dict):
        """Mode m5_confirm: apakah SEKARANG saatnya masuk pasar?

        Dihitung ulang dari bar M5 tiap poll (bukan disimpan sebagai keadaan), supaya
        restart di tengah jalan tidak mengubah hasil — filosofi yang sama dengan
        _setup_terkini().

        Kembalikan (masuk: bool, alasan: str).
        """
        df = self.data.recent_bars(self.symbol, 20000)
        if df.empty:
            return False, "tidak ada data M1"
        m5 = (df.resample("5min").agg({"open": "first", "high": "max",
                                       "low": "min", "close": "last"})
                .dropna(subset=["open"]))
        now = pd.Timestamp.now("UTC")
        if len(m5) > 1 and m5.index[-1] == now.floor("5min"):
            m5 = m5.iloc[:-1]                      # buang bar berjalan
        seg = m5.loc[setup["bos_time"]:setup["expiry_time"]]
        if len(seg) < self.k * 2 + 2:
            return False, "bar M5 belum cukup"

        arah = setup["arah"]; px = setup["price"]
        hi = seg["high"].to_numpy(); lo = seg["low"].to_numpy()
        c = seg["close"].to_numpy(); n = len(seg)

        # 1) harga harus SUDAH menyentuh zona
        sentuh = None
        for j in range(1, n):
            if (lo[j] <= px) if arah == 1 else (hi[j] >= px):
                sentuh = j; break
        if sentuh is None:
            return False, "zona belum tersentuh"

        # 2) BOS M5 searah dalam jendela konfirmasi setelah sentuhan
        sh, sl_p = self._pivots(hi, lo, self.k)
        lvl = self._level_terkonfirmasi(sh if arah == 1 else sl_p, n)
        batas = min(sentuh + self.konfirm_bars_m5, n)
        konfirm = None
        for j in range(sentuh, batas):
            ref = lvl[j]
            if ref != ref:
                continue
            if (arah == 1 and c[j] > ref) or (arah == -1 and c[j] < ref):
                konfirm = j; break
        if konfirm is None:
            habis = (batas >= n and n - sentuh >= self.konfirm_bars_m5)
            return False, ("jendela konfirmasi HABIS tanpa BOS M5" if habis
                           else f"menunggu BOS M5 ({n - sentuh}/{self.konfirm_bars_m5} bar)")

        # 3) konfirmasi harus BARU — kalau sudah lewat lama, harganya basi
        umur = (n - 1) - konfirm
        if umur > self.konfirm_max_umur:
            return False, f"konfirmasi sudah lewat {umur} bar M5 -> harga basi, lewati"
        # 4) jangan masuk kalau harga sudah melewati SL/TP
        px_now = c[-1]
        if (arah == 1 and (px_now >= setup["tp"] or px_now <= setup["sl"])) or \
           (arah == -1 and (px_now <= setup["tp"] or px_now >= setup["sl"])):
            return False, "harga sudah di luar SL/TP"
        return True, f"BOS M5 terkonfirmasi ({umur} bar lalu)"

    def _kirim_market(self, mt5, arah: int, sl: float, tp: float) -> bool:
        info = mt5.symbol_info(self.mt5_symbol)
        tick0 = mt5.symbol_info_tick(self.mt5_symbol)
        px0 = (tick0.ask if arah == 1 else tick0.bid) if tick0 else 0.0
        lot = self._hitung_lot(mt5, px0, sl) if px0 else self.lot
        filling = (mt5.ORDER_FILLING_IOC if info and (info.filling_mode & 2)
                   else mt5.ORDER_FILLING_FOK)
        tick = mt5.symbol_info_tick(self.mt5_symbol)
        harga = tick.ask if arah == 1 else tick.bid
        req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": self.mt5_symbol,
               "volume": lot,
               "type": mt5.ORDER_TYPE_BUY if arah == 1 else mt5.ORDER_TYPE_SELL,
               "price": harga, "sl": round(sl, 2), "tp": round(tp, 2),
               "deviation": 20, "magic": self.magic,
               "type_time": mt5.ORDER_TIME_GTC, "type_filling": filling,
               "comment": "smc_m5"}
        return self._send(mt5, req, f"MARKET {'BUY' if arah == 1 else 'SELL'} (konfirmasi M5)")

    def _hitung_lot(self, mt5, price: float, sl: float) -> float:
        """Lot dari risiko sesuai balance. Kembalikan lot tetap kalau risk_pct = 0."""
        if self.risk_pct <= 0:
            return self.lot
        try:
            jarak = abs(price - sl)
            if jarak <= 0:
                return self.lot
            ai = mt5.account_info(); info = mt5.symbol_info(self.mt5_symbol)
            if ai is None or info is None:
                return self.lot
            target = ai.balance * self.risk_pct
            mentah = target / (jarak * self.contract)
            step = float(getattr(info, "volume_step", 0.01)) or 0.01
            vmin = float(getattr(info, "volume_min", 0.01)) or 0.01
            vmax = min(self.lot_maks, float(getattr(info, "volume_max", 100.0)))
            lot = np.floor(mentah / step) * step
            lot = max(vmin, min(vmax, lot))
            lot = round(lot, 2)
            risiko = jarak * self.contract * lot
            logger.info(f"[smcmgr] {self.magic} sizing: balance ${ai.balance:.2f} x "
                        f"{self.risk_pct:.1%} = ${target:.2f} target | SL ${jarak:.2f} "
                        f"-> lot {lot} (risiko ${risiko:.2f} = {100*risiko/ai.balance:.1f}%)")
            return lot
        except Exception:
            logger.exception("[smcmgr] gagal menghitung lot, pakai lot tetap")
            return self.lot

    def _trade_hari_ini(self, mt5) -> int:
        """Jumlah TRADE hari ini untuk magic ini, dibaca dari riwayat deal MT5.

        Sengaja TIDAK bergantung pada file state. State bisa basi: pada 2026-08-13
        sebuah limit order dipasang lalu DIBATALKAN saat mode diganti ke m5_confirm,
        tapi penghitungnya sudah terlanjur naik -> SMC kehilangan 1 dari 2 jatah
        harian tanpa pernah trade. Riwayat deal adalah kebenaran yang tidak bisa
        basi, dan sesuai permintaan user: yang dibatasi adalah TRADE, bukan order.

        Kalau MT5 tidak menjawab, kembalikan -1 supaya pemanggil memakai state
        sebagai cadangan (fail-safe: lebih baik memakai angka lama daripada
        menganggap nol lalu melewati batas).
        """
        try:
            now = pd.Timestamp.now("UTC")
            awal = now.normalize()
            off = self.data._server_offset_hours(mt5, self.mt5_symbol)
            a = (awal + pd.Timedelta(hours=off)).to_pydatetime()
            b = (now + pd.Timedelta(hours=off + 1)).to_pydatetime()
            deals = mt5.history_deals_get(a, b)
            if deals is None:
                return -1
            # entry=0 (DEAL_ENTRY_IN) = pembukaan posisi; itulah satu "trade"
            return sum(1 for d in deals
                       if d.magic == self.magic and d.entry == 0)
        except Exception:
            logger.exception("[smcmgr] gagal membaca riwayat deal (pakai state)")
            return -1

    def _bar_selesai(self):
        df = self.data.recent_bars(self.symbol, self.history_bars)
        if df.empty:
            return None
        h = (df.resample(self.timeframe)
               .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
               .dropna(subset=["open"]))
        now = pd.Timestamp.now("UTC")
        cb = now.floor(self.timeframe)
        # buang bar berjalan: sinyal HANYA dari bar yang sudah tertutup
        return h.iloc[:-1] if (len(h) > 1 and h.index[-1] == cb) else h

    # ---------------------------------------------------------------- MT5
    def _send(self, mt5, req, what) -> bool:
        if self.dry_run:
            logger.info(f"[smcmgr] DRY-RUN {what}: {self._fmt(req)}")
            return True
        r = mt5.order_send(req)
        ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
        logger.info(f"[smcmgr] {what}: {self._fmt(req)} -> retcode={getattr(r, 'retcode', None)} "
                    f"{'OK' if ok else 'FAIL ' + str(getattr(r, 'comment', ''))}")
        return ok

    @staticmethod
    def _fmt(req):
        keys = ("type", "price", "sl", "tp", "order", "expiration")
        return " ".join(f"{k}={req[k]}" for k in keys if k in req)

    def _place(self, mt5, otype, price, sl, tp, expiry_utc: pd.Timestamp) -> bool:
        lot = self._hitung_lot(mt5, price, sl)
        # MT5 `expiration` memakai WAKTU SERVER BROKER (FBS UTC+3), bukan UTC.
        # Pakai detektor offset yang SAMA dengan DataProvider supaya tidak ada
        # sumber kebenaran kedua yang bisa menyimpang saat DST berganti.
        off = self.data._server_offset_hours(mt5, self.mt5_symbol)
        # HARUS int epoch, BUKAN objek datetime. Diuji langsung ke FBS 2026-08-13:
        #   datetime tz-aware -> order_send() None, last_error (-2, 'Invalid "expiration" argument')
        #   datetime naive    -> None, error yang sama
        #   int epoch         -> retcode 10009 diterima, broker menyimpan waktu yang benar
        # Ini tidak terlihat di liquidity_manager karena dia memakai ORDER_TIME_GTC.
        exp_epoch = int(expiry_utc.timestamp()) + off * 3600
        req = {"action": mt5.TRADE_ACTION_PENDING, "symbol": self.mt5_symbol,
               "volume": lot, "type": otype,
               "price": round(price, 2), "sl": round(sl, 2), "tp": round(tp, 2),
               "magic": self.magic, "type_time": mt5.ORDER_TIME_SPECIFIED,
               "expiration": exp_epoch, "comment": "smc_ob"}
        nama = "BUY_LIMIT" if otype == mt5.ORDER_TYPE_BUY_LIMIT else "SELL_LIMIT"
        exp_srv = expiry_utc + pd.Timedelta(hours=off)
        return self._send(mt5, req, f"PLACE {nama} exp={exp_srv:%Y-%m-%d %H:%M} srv "
                                    f"({expiry_utc:%H:%M} UTC)")

    def _cancel(self, mt5, ticket):
        self._send(mt5, {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}, "CANCEL pending")

    # ---------------------------------------------------------------- loop
    def poll_once(self, mt5) -> None:
        poss = mt5.positions_get(symbol=self.mt5_symbol)
        pends = mt5.orders_get(symbol=self.mt5_symbol)
        if poss is None or pends is None:
            return                                   # MT5 belum siap -> lewati
        my_pos = [p for p in poss if p.magic == self.magic]
        my_pend = [o for o in pends if o.magic == self.magic]

        if my_pos:                                   # satu posisi -> broker yang urus SL/TP
            for o in my_pend:
                self._cancel(mt5, o.ticket)
            return

        if _governor_menjeda():
            for o in my_pend:
                self._cancel(mt5, o.ticket)      # jangan tinggalkan pending saat dijeda
            return

        h = self._bar_selesai()
        if h is None or h.empty:
            return
        setup = self._setup_terkini(h)

        if setup is None:
            for o in my_pend:                        # tidak ada setup aktif -> bersihkan
                self._cancel(mt5, o.ticket)
            return

        st = self._baca_state()
        bos_key = setup["bos_time"].isoformat()

        # ---- mode m5_confirm: tidak ada pending; pantau lalu kirim MARKET ----------
        if self.entry_mode == "m5_confirm":
            for o in my_pend:                    # mode ini tidak memakai pending
                self._cancel(mt5, o.ticket)
            if st.get("bos") == bos_key and st.get("terkirim"):
                return
            if pd.Timestamp.now("UTC") >= setup["expiry_time"]:
                self._tulis_state({**st, "bos": bos_key, "terkirim": True,
                                   "dilewati_karena": "zona kedaluwarsa tanpa konfirmasi"})
                return
            hari = pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
            # Sumber kebenaran = riwayat deal MT5 (TRADE nyata), bukan file state.
            # State dipakai hanya kalau MT5 tidak menjawab.
            nyata = self._trade_hari_ini(mt5)
            jumlah = nyata if nyata >= 0 else (
                int(st.get("jumlah", 0)) if st.get("hari") == hari else 0)
            if jumlah >= self.max_per_day:
                if st.get("alasan_terakhir") != "batas harian":
                    logger.info(f"[smcmgr] {self.magic} batas {self.max_per_day} "
                                f"trade/hari tercapai ({jumlah}) -> zona dilewati")
                    self._tulis_state({**st, "alasan_terakhir": "batas harian",
                                       "hari": hari, "jumlah": jumlah})
                return
            siap, alasan = self._konfirmasi_m5(setup)
            if not siap:
                if st.get("alasan_terakhir") != alasan:      # jangan spam log
                    logger.info(f"[smcmgr] {self.magic} zona {bos_key}: {alasan}")
                    self._tulis_state({**st, "bos": bos_key, "alasan_terakhir": alasan,
                                       "hari": hari, "jumlah": jumlah})
                return
            logger.info(f"[smcmgr] {self.magic} zona {bos_key}: {alasan} -> MASUK PASAR")
            mesin = {"sl": round(setup["sl"], 2), "tp": round(setup["tp"], 2),
                     "expiry_bars": self.expiry_bars}
            tick = mt5.symbol_info_tick(self.mt5_symbol)
            rr = self.rr_agent.nilai(arah=setup["arah"], price=round(setup["price"], 2),
                                     mesin=mesin, symbol=self.symbol,
                                     expiry_utc=setup["expiry_time"],
                                     tick_bid=float(tick.bid) if tick else 0.0)
            if rr.get("skip"):
                self._tulis_state({**st, "bos": bos_key, "terkirim": True, "hari": hari,
                                   "jumlah": jumlah, "dilewati_karena": "agent SKIP"})
                return
            ok = self._kirim_market(mt5, setup["arah"], float(rr["sl"]), float(rr["tp"]))
            if ok:
                self._tulis_state({"bos": bos_key, "terkirim": True, "mode": "m5_confirm",
                                   "arah": setup["arah"], "zona": setup["price"],
                                   "sl": rr["sl"], "tp": rr["tp"],
                                   "sumber_rr": rr.get("sumber"),
                                   "hari": hari, "jumlah": jumlah + 1})
                logger.info(f"[smcmgr] {self.magic} setup ke-{jumlah + 1}/{self.max_per_day} "
                            f"hari ini, RR dari {rr.get('sumber')}")
            return

        otype = mt5.ORDER_TYPE_BUY_LIMIT if setup["arah"] == 1 else mt5.ORDER_TYPE_SELL_LIMIT

        # pending yang sudah ada untuk BOS yang sama -> biarkan, broker pegang expiry-nya
        if my_pend and st.get("bos") == bos_key:
            return
        for o in my_pend:                            # BOS baru -> buang order lama
            self._cancel(mt5, o.ticket)
        if st.get("bos") == bos_key and st.get("terkirim"):
            return                                   # sudah pernah dikirim & sudah hilang

        tick = mt5.symbol_info_tick(self.mt5_symbol)
        info = mt5.symbol_info(self.mt5_symbol)
        if tick and info:
            min_dist = info.trade_stops_level * info.point
            if otype == mt5.ORDER_TYPE_BUY_LIMIT and setup["price"] >= tick.ask - min_dist:
                logger.info(f"[smcmgr] zona {setup['price']:.2f} terlalu dekat ask "
                            f"{tick.ask:.2f} -> tunggu")
                return
            if otype == mt5.ORDER_TYPE_SELL_LIMIT and setup["price"] <= tick.bid + min_dist:
                logger.info(f"[smcmgr] zona {setup['price']:.2f} terlalu dekat bid "
                            f"{tick.bid:.2f} -> tunggu")
                return

        if pd.Timestamp.now("UTC") >= setup["expiry_time"]:
            logger.info(f"[smcmgr] setup BOS {bos_key} sudah lewat masa berlaku -> lewati")
            self._tulis_state({**st, "bos": bos_key, "terkirim": True})
            return

        # --- batas setup per hari ---------------------------------------------
        hari = pd.Timestamp.now("UTC").strftime("%Y-%m-%d")
        jumlah = int(st.get("jumlah", 0)) if st.get("hari") == hari else 0
        if jumlah >= self.max_per_day:
            logger.info(f"[smcmgr] batas {self.max_per_day} setup/hari sudah tercapai "
                        f"({jumlah} hari ini) -> zona {bos_key} DILEWATI")
            self._tulis_state({**st, "bos": bos_key, "terkirim": True,
                               "hari": hari, "jumlah": jumlah,
                               "dilewati_karena": "batas harian"})
            return

        # --- agent RR: boleh menyesuaikan SL/TP/time-exit di dalam batas keras --
        tick = mt5.symbol_info_tick(self.mt5_symbol)
        mesin = {"sl": round(setup["sl"], 2), "tp": round(setup["tp"], 2),
                 "expiry_bars": self.expiry_bars}
        rr = self.rr_agent.nilai(arah=setup["arah"], price=round(setup["price"], 2),
                                 mesin=mesin, symbol=self.symbol,
                                 expiry_utc=setup["expiry_time"],
                                 tick_bid=float(tick.bid) if tick else 0.0)
        if rr.get("skip"):
            logger.info(f"[smcmgr] agent menyarankan LEWATI zona {bos_key} :: {rr['reason'][:160]}")
            self._tulis_state({**st, "bos": bos_key, "terkirim": True, "hari": hari,
                               "jumlah": jumlah, "dilewati_karena": "agent SKIP"})
            return
        sl_pakai, tp_pakai = float(rr["sl"]), float(rr["tp"])
        # time-exit bisa digeser agent -> hitung ulang saat kedaluwarsa dari bar BOS
        exp_pakai = setup["bos_time"] + int(rr["expiry_bars"]) * pd.Timedelta(self.timeframe)
        if exp_pakai <= pd.Timestamp.now("UTC"):
            exp_pakai = setup["expiry_time"]      # agent memendekkan sampai lewat -> pakai asli

        ok = self._place(mt5, otype, setup["price"], sl_pakai, tp_pakai, exp_pakai)
        if ok:
            self._tulis_state({"bos": bos_key, "terkirim": True,
                               "arah": setup["arah"], "price": setup["price"],
                               "sl": sl_pakai, "tp": tp_pakai,
                               "sumber_rr": rr.get("sumber"),
                               "expiry_utc": exp_pakai.isoformat(),
                               "hari": hari, "jumlah": jumlah + 1})
            logger.info(f"[smcmgr] setup ke-{jumlah + 1}/{self.max_per_day} hari ini "
                        f"({hari}), RR dari {rr.get('sumber')}")

    def deskripsi(self) -> str:
        return (f"magic={self.magic} {self.symbol} tf={self.timeframe} k={self.k} "
                f"rr={self.rr} expiry={self.expiry_bars} bar fvg={self.use_fvg} "
                f"sweep={self.use_sweep} max/hari={self.max_per_day} "
                f"dry_run={self.dry_run}")


def run_semua(managers: list["SmcLimitManager"]) -> None:
    """Satu proses menjalankan SEMUA aliran SMC.

    Sengaja satu proses, bukan satu proses per aliran: state, log, dan pengawasan
    watchdog jadi satu tempat, dan kegagalan MT5 tertangani seragam. Kalau satu
    aliran melempar exception, aliran lain TETAP jalan (ditangkap per-manager).
    """
    import MetaTrader5 as mt5
    if not mt5.initialize():
        logger.error(f"[smcmgr] MT5 init gagal: {mt5.last_error()}"); return
    for m in managers:
        logger.info(f"[smcmgr] hidup. {m.deskripsi()}")
    poll = min(m.poll for m in managers)
    try:
        while True:
            for m in managers:
                try:
                    m.poll_once(mt5)
                except Exception:
                    logger.exception(f"[smcmgr] poll error magic={m.magic} (lanjut)")
            time.sleep(poll)
    finally:
        mt5.shutdown()


def main() -> None:
    cfg = load_config()
    specs = [s for s in cfg["live"]["strategies"]
             if s.get("type") == "smclimit" and s.get("enabled", False)]
    if not specs:
        logger.info("[smcmgr] tidak ada slot smclimit aktif di config. Keluar."); return
    magics = [s["magic"] for s in specs]
    if len(set(magics)) != len(magics):
        logger.error(f"[smcmgr] magic BENTROK di slot smclimit: {magics}. Keluar."); return
    run_semua([SmcLimitManager(cfg, s) for s in specs])


if __name__ == "__main__":
    main()
