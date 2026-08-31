"""Pencatat regime pasar untuk akun real -- MENGAMATI SAJA, tidak pernah bertindak.

KENAPA ALAT INI ADA, dan bukti yang membenarkannya
--------------------------------------------------
Riset sebelumnya membuktikan regime TIDAK bisa menebak basket mana yang akan
rugi. ER saat basket dibuka: pemenang 0,248 vs pecundang 0,251, p = 0,86.
Tidak terbedakan. Sama seperti temuan di tingkat bulan (korelasi tren +0,14).

Tapi regime SANGAT menentukan BESAR untungnya. Empat tahun, konfigurasi live
(FINAL.set, gate mati), 741 basket dari report backtest tick asli:

    regime      basket   WR%      total     per basket
    RANGING        234   91,9   +1101,53        +4,71
    CAMPURAN       256   88,7     +51,37        +0,20
    TREN           251   88,4    -168,99        -0,67

Seluruh keuntungan datang dari pasar RANGING; basket di pasar tren rugi bersih
selama empat tahun. Mann-Whitney RANGING > TREN p = 0,0124. Membuang 5% basket
terburuk tiap kelompok TIDAK mengubah urutannya ($8,69 > $3,97 > $3,18), jadi
ini bukan efek segelintir kerugian besar.

Konfirmasi silang: di 2023 kelompok TREN sendirian rugi -$308,71, sementara
riset regime gate secara terpisah menemukan gate menyala memperbaiki 2023
sebesar +$304,90. Dua metode berbeda, selisih $4.

KENAPA HANYA MENGAMATI
----------------------
Alat ini TIDAK mengubah setelan apa pun dan tidak menutup posisi apa pun. Dia
menulis catatan supaya pemilik bisa memutuskan sendiri kapan perlu riset ulang
atau mengubah parameter. Mengubah setelan live tanpa diminta pernah merugikan
$209 di proyek ini.

DEFINISI REGIME
---------------
Efficiency Ratio (Kaufman) di XAUUSD H1, jendela 20 bar:
    ER = |close[0] - close[-20]| / jumlah |perubahan tiap bar|
Mendekati 1 = harga bergerak lurus (TREN). Mendekati 0 = bolak-balik (RANGING).

Ambangnya BUKAN karangan -- diambil dari tersil sebaran nyata 50.000 bar H1
(2018-03 sampai 2026-08), sehingga tiap regime kira-kira sepertiga waktu:
    RANGING  <= 0,134
    CAMPURAN
    TREN     >  0,306
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from loguru import logger

STATE = Path(r"C:\Quant\_MONITOR\regime.json")
HISTORY = Path(r"C:\Quant\_MONITOR\regime_history.jsonl")

SYMBOL = "XAUUSD"
ER_BARS = 20            # jendela efficiency ratio (bar H1)
ATR_BARS = 14
ER_RANGING = 0.134      # tersil ke-33 dari 50.000 bar H1
ER_TREN = 0.306         # tersil ke-67

# Berapa bar H1 berturut-turut label baru harus bertahan sebelum diakui sebagai
# pergantian regime. Tanpa ini labelnya berkedip-kedip tiap jam dan catatannya
# jadi tidak berguna.
CONFIRM_BARS = 2

# Untung per basket yang TERCATAT di backtest empat tahun. Dipakai hanya untuk
# menyebut ekspektasi di laporan -- bukan ramalan, dan bukan dasar tindakan.
HARAPAN_PER_BASKET = {"RANGING": 4.71, "CAMPURAN": 0.20, "TREN": -0.67}


def klasifikasi(er: float) -> str:
    if er <= ER_RANGING:
        return "RANGING"
    if er > ER_TREN:
        return "TREN"
    return "CAMPURAN"


class RegimeWatcher:
    """Mencatat regime XAUUSD dan kapan dia berganti. Tidak pernah bertindak."""

    # CATATAN WAKTU -- sumber kesalahan yang sudah terjadi sekali.
    # Epoch bar MT5 adalah WAKTU SERVER (UTC+3) yang dikemas seolah UTC. Versi
    # pertama menempelkan tzinfo=UTC pada `since`, sehingga terlihat 3 jam di
    # masa depan dan umur regime tercetak "-1 hari 22 jam". Jadi: semua stempel
    # di sini adalah WAKTU SERVER, dan umur dihitung dari selisih EPOCH (yang
    # konsisten satu sama lain), bukan dari jam sistem.

    def __init__(self) -> None:
        self.regime: str | None = None        # label yang sudah dikukuhkan
        self.since: str | None = None         # kapan label itu mulai (waktu SERVER)
        self.since_epoch: int | None = None
        self.er: float | None = None
        self.atr: float | None = None
        self.last_bar: int | None = None      # epoch bar H1 terakhir yang diolah
        self._pending: str | None = None
        self._pending_n: int = 0
        self.changes: int = 0
        self._load()

    # ---------------------------------------------------------------- state
    def _load(self) -> None:
        try:
            d = json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:                                    # noqa: BLE001
            return
        self.regime = d.get("regime")
        self.since = d.get("since")
        self.since_epoch = d.get("since_epoch")
        self.er = d.get("er")
        self.atr = d.get("atr")
        self.last_bar = d.get("last_bar")
        self.changes = int(d.get("changes") or 0)

    def _save(self) -> None:
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps(self.state(), indent=2), encoding="utf-8")
        except Exception:                                    # noqa: BLE001
            logger.exception("[regime] gagal menulis state (diabaikan)")

    def _append_history(self, bar_time: datetime, er: float, atr: float,
                        label: str, berganti: bool) -> None:
        try:
            HISTORY.parent.mkdir(parents=True, exist_ok=True)
            with HISTORY.open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "bar": bar_time.isoformat(),        # waktu SERVER (UTC+3)
                    "er": round(er, 4),
                    "atr": round(atr, 2),
                    "regime": label,
                    "berganti": berganti,
                }) + "\n")
        except Exception:                                    # noqa: BLE001
            logger.exception("[regime] gagal menulis history (diabaikan)")

    # ---------------------------------------------------------------- hitung
    @staticmethod
    def _hitung(rates) -> tuple[float, float]:
        """ER dan ATR dari bar H1 yang SUDAH TUTUP.

        rates[-1] adalah bar yang masih terbentuk, jadi dibuang. Memakai bar
        berjalan berarti memakai data yang belum final -- kesalahan yang sudah
        pernah menghasilkan sinyal hantu di proyek ini.
        """
        c = [float(x) for x in rates["close"]][:-1]
        h = [float(x) for x in rates["high"]][:-1]
        l = [float(x) for x in rates["low"]][:-1]

        # Pembilang mencakup ER_BARS langkah: dari c[-1-ER_BARS] sampai c[-1].
        # Penyebut HARUS mencakup langkah yang sama persis, yaitu indeks
        # len(c)-ER_BARS .. len(c)-1. Versi pertama memakai +1 di sini sehingga
        # penyebutnya satu langkah lebih pendek dan ER selalu ~5% terlalu tinggi
        # -- condong melabeli pasar sebagai TREN. Ditangkap test_garis_lurus.
        moves = [abs(c[i] - c[i - 1]) for i in range(len(c) - ER_BARS, len(c))]
        denom = sum(moves)
        er = abs(c[-1] - c[-1 - ER_BARS]) / denom if denom > 0 else 0.0

        trs = []
        for i in range(len(c) - ATR_BARS, len(c)):
            trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
        atr = sum(trs) / len(trs) if trs else 0.0
        return er, atr

    # ---------------------------------------------------------------- poll
    def poll(self) -> dict:
        """Dipanggil dari heartbeat. Bekerja hanya saat ada bar H1 baru."""
        from .book import _mt5                       # terminal live, tester ditolak

        mt5 = _mt5()
        need = ER_BARS + ATR_BARS + 5
        rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_H1, 0, need)
        if rates is None or len(rates) < need:
            return self.state()

        bar_epoch = int(rates["time"][-2])            # bar tertutup terakhir
        if self.last_bar is not None and bar_epoch <= self.last_bar:
            return self.state()                       # belum ada bar baru

        er, atr = self._hitung(rates)
        label = klasifikasi(er)
        bar_time = datetime.utcfromtimestamp(bar_epoch)

        berganti = False
        if self.regime is None:
            self.regime = label
            self.since, self.since_epoch = bar_time.isoformat(), bar_epoch
            logger.info(f"[regime] mulai mencatat: {label} (ER {er:.3f})")
        elif label != self.regime:
            if self._pending == label:
                self._pending_n += 1
            else:
                self._pending, self._pending_n = label, 1
            if self._pending_n >= CONFIRM_BARS:
                lama = self.regime
                self.regime = label
                self.since, self.since_epoch = bar_time.isoformat(), bar_epoch
                self.changes += 1
                self._pending, self._pending_n = None, 0
                berganti = True
                logger.warning(
                    f"[regime] BERGANTI {lama} -> {label} pada {bar_time} UTC "
                    f"(ER {er:.3f}, ATR ${atr:.2f}). Harapan per basket "
                    f"${HARAPAN_PER_BASKET.get(label, 0.0):+.2f} menurut backtest "
                    f"4 tahun. TIDAK ada setelan yang diubah -- ini catatan saja.")
        else:
            self._pending, self._pending_n = None, 0

        self.er, self.atr, self.last_bar = er, atr, bar_epoch
        self._append_history(bar_time, er, atr, label, berganti)
        self._save()
        return self.state()

    # ---------------------------------------------------------------- lapor
    def state(self) -> dict:
        # Umur regime dari selisih EPOCH bar, bukan dari jam sistem: keduanya
        # waktu server, jadi selisihnya benar tanpa perlu tahu offset apa pun.
        umur = None
        if self.since_epoch is not None and self.last_bar is not None:
            umur = round((self.last_bar - self.since_epoch) / 3600.0, 1)
        return {
            "regime": self.regime,
            "since": self.since,              # waktu SERVER (UTC+3), bukan UTC
            "since_epoch": self.since_epoch,
            "umur_jam": umur,
            "er": None if self.er is None else round(self.er, 4),
            "atr": None if self.atr is None else round(self.atr, 2),
            "last_bar": self.last_bar,
            "changes": self.changes,
            "pending": self._pending,
            "pending_bars": self._pending_n,
            "harapan_per_basket": (None if self.regime is None
                                   else HARAPAN_PER_BASKET.get(self.regime)),
            "ambang": {"ranging_max": ER_RANGING, "tren_min": ER_TREN},
        }
