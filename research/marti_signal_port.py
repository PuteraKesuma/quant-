"""Port Python dari sinyal 'Semi Marti Cuan v10' - DIVALIDASI dulu, baru dipakai riset.

KENAPA PORT, BUKAN LANGSUNG PAKAI MT5 TESTER:
Tester MT5 setia tapi lambat - 7,5 bulan makan 15 menit, 5 tahun ~90 menit. Untuk
MENCARI struktur TP/SL yang benar kita perlu puluhan percobaan; di tester itu berhari-hari.
Jadi: eksplorasi cepat di Python, lalu KONFIRMASI pemenangnya di MT5 tester.

KENAPA VALIDASI DULU:
Proyek ini sudah dua kali rugi karena reimplementasi diam-diam salah (ORB/ZREV ditulis
ulang -> 0 trade karena resample harian menyisipkan akhir pekan kosong). Port yang
"kelihatan benar" tapi meleset akan menghasilkan riset yang seluruhnya sampah. Jadi
sebelum satu pun eksperimen dijalankan, port ini harus mereproduksi hasil MT5 yang
SUDAH DIKETAHUI:
    XAUUSD M15, 2026-01-01 .. 2026-08-11  ->  49 seri sinyal (98 trade dual-entry)

SPESIFIKASI SINYAL (dibaca baris demi baris dari .mq5, bukan ditebak):
  sourceRaw[b] = close[b]                      (InpMASource=0 = PRICE)
  smaRaw[b]    = rata-rata close[b .. b+20]    (SMA21, indeks seri: b=0 bar tertutup terakhir)
  smaNorm[b]   = 100*(smaRaw[b]-min)/(max-min) atas smaRaw[b .. b+99], diklem 0..100
  -> ini Stochastic dari SMA21 atas jendela 100 bar.
  mode 2 (SMA only): rawSell = smaNorm>=80 ; rawBuy = smaNorm<=20

KONFIRMASI (InpRequireBreakConfirm=true) - mesin keadaan, WAJIB ditiru persis:
  rawSell & belum menunggu      -> pasang penantian (belum entry)
  rawSell hilang & sedang tunggu -> kalau kedua norm < 80 -> tandai 'pulled'
  rawSell muncul lagi & pulled   -> SINYAL SELL
  rawBuy membatalkan penantian SELL, dan sebaliknya
  CATATAN: syarat pullback memeriksa macdNorm DAN smaNorm, walaupun mode 2 hanya
  memakai smaNorm untuk sinyal mentahnya. Kejanggalan itu ADA di EA, jadi ditiru.

Filter jam: 9..23 waktu SERVER broker (FBS = UTC+3).

Jalankan: python research/marti_signal_port.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
SRV_OFFSET_H = 3          # FBS server = UTC+3
LVL_HI, LVL_LO = 80.0, 20.0
SMA_PERIOD, NORM_PERIOD = 21, 100
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 5, 13, 9
HOUR_START, HOUR_END = 9, 23


def load_m15() -> pd.DataFrame:
    """Bar M15 dari MT5 SENDIRI, bukan dari duckdb Dukascopy.

    KENAPA: percobaan pertama memakai duckdb dan menghasilkan 29 sinyal vs 49 milik
    tester - meleset 41%. Sebabnya bukan rumusnya, tapi DATANYA. Feed Dukascopy dan
    feed FBS punya close yang sedikit berbeda, dan sinyal ini adalah perbandingan
    terhadap ambang tetap (80/20) dari nilai ter-normalisasi. Di dekat ambang, beda
    beberapa sen saja membalik sinyal. Membandingkan port terhadap tester hanya sah
    kalau keduanya membaca bar yang SAMA.
    """
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 gagal init: {mt5.last_error()}")
    # copy_rates_range rusak di FBS (catatan lama proyek) -> pakai from_pos
    # Batas keras terminal: 20.000 lolos, 100.000 -> "Invalid params". Segitu ~7 bulan
    # M15, cukup untuk validasi 2026 (mundur sampai Okt 2025, termasuk pemanasan 121 bar).
    # Untuk riset 5 tahun nanti perlu MaxBars dinaikkan atau ditarik per potongan.
    bars = mt5.copy_rates_from_pos("XAUUSD", mt5.TIMEFRAME_M15, 0, 20_000)
    mt5.shutdown()
    if bars is None or len(bars) == 0:
        raise SystemExit("tidak ada bar M15 dari MT5")
    df = pd.DataFrame(bars)
    # `time` bar MT5 memakai waktu SERVER; kembalikan ke UTC supaya konsisten
    df["ts"] = pd.to_datetime(df["time"] - SRV_OFFSET_H * 3600, unit="s", utc=True)
    return df.set_index("ts")[["open", "high", "low", "close"]].sort_index()


def ema_last(x: np.ndarray, period: int) -> float:
    """EMA seperti EMA_from_prices di EA: deret dibalik (indeks 0 = terbaru)."""
    k = 2.0 / (period + 1.0)
    e = x[-1]
    for v in x[-2::-1]:
        e = v * k + e * (1 - k)
    return e


def build_signals(df: pd.DataFrame) -> pd.DataFrame:
    """Kembalikan df + kolom smaNorm, macdNorm, dan sinyal terkonfirmasi."""
    c = df["close"].to_numpy()
    n = len(c)

    # --- SMA21 lalu normalisasi min-max 100 bar (vektor, tapi semantik sama) ---
    sma = pd.Series(c).rolling(SMA_PERIOD).mean().to_numpy()
    s = pd.Series(sma)
    mn = s.rolling(NORM_PERIOD).min().to_numpy()
    mx = s.rolling(NORM_PERIOD).max().to_numpy()
    rng = mx - mn
    sma_norm = np.where(rng != 0, (sma - mn) / np.where(rng == 0, 1, rng) * 100.0, 50.0)
    sma_norm = np.clip(sma_norm, 0.0, 100.0)

    # --- MACD signal lalu normalisasi min-max 100 bar ---
    ef = pd.Series(c).ewm(span=MACD_FAST, adjust=False).mean().to_numpy()
    es = pd.Series(c).ewm(span=MACD_SLOW, adjust=False).mean().to_numpy()
    macd_main = ef - es
    # EA memakai RATA-RATA SEDERHANA 9 bar dari main, bukan EMA - lihat signalRaw[]
    macd_sig = pd.Series(macd_main).rolling(MACD_SIGNAL).mean().to_numpy()
    ms = pd.Series(macd_sig)
    mn2 = ms.rolling(NORM_PERIOD).min().to_numpy()
    mx2 = ms.rolling(NORM_PERIOD).max().to_numpy()
    rng2 = mx2 - mn2
    macd_norm = np.where(rng2 != 0, (macd_sig - mn2) / np.where(rng2 == 0, 1, rng2) * 100.0, 50.0)
    macd_norm = np.clip(macd_norm, 0.0, 100.0)

    out = df.copy()
    out["smaNorm"] = sma_norm
    out["macdNorm"] = macd_norm

    # --- mesin keadaan konfirmasi, ditiru persis dari EA ---
    srv_hour = (out.index + pd.Timedelta(hours=SRV_OFFSET_H)).hour
    sig = np.zeros(n, dtype=int)     # +1 buy, -1 sell
    wait_s = wait_b = False
    pull_s = pull_b = False
    for i in range(n):
        if np.isnan(sma_norm[i]) or np.isnan(macd_norm[i]):
            continue
        # ---------------------------------------------------------------------
        #  FILTER JAM MEMBEKUKAN MESIN KEADAAN, bukan sekadar menolak entry.
        #  Di EA baris 1364, IsWithinTradingHours() melakukan EARLY RETURN sebelum
        #  sinyal dihitung sama sekali. Jadi di luar jam 9-23 server, waitSellConfirm
        #  dan sellPulled DIBEKUKAN - tidak maju, tidak reset.
        #  Percobaan pertama memajukan state di luar jam lalu menolkan sinyalnya di
        #  akhir; itu menghanguskan rangkaian yang seharusnya diselesaikan EA pada
        #  jam berikutnya, dan port kehilangan ~14 sinyal karenanya.
        # ---------------------------------------------------------------------
        if not (HOUR_START <= srv_hour[i] <= HOUR_END):
            continue
        raw_sell = sma_norm[i] >= LVL_HI          # mode 2: SMA saja
        raw_buy = sma_norm[i] <= LVL_LO

        # ---- sisi SELL ----
        if raw_sell:
            if not wait_s:
                wait_s, pull_s = True, False
            elif pull_s:
                sig[i] = -1
                wait_s, pull_s = False, False
        else:
            if wait_s and not pull_s:
                if macd_norm[i] < LVL_HI and sma_norm[i] < LVL_HI:
                    pull_s = True

        # ---- sisi BUY ----
        if raw_buy:
            if not wait_b:
                wait_b, pull_b = True, False
            elif pull_b:
                if sig[i] == 0:
                    sig[i] = 1
                wait_b, pull_b = False, False
        else:
            if wait_b and not pull_b:
                if macd_norm[i] > LVL_LO and sma_norm[i] > LVL_LO:
                    pull_b = True

        # ---- saling membatalkan ----
        if raw_sell and wait_b:
            wait_b, pull_b = False, False
        if raw_buy and wait_s:
            wait_s, pull_s = False, False

    # filter jam sudah diterapkan DI DALAM loop (membekukan state), jangan diulang di sini
    out["sinyal"] = sig
    return out


def main():
    df = load_m15()
    sg = build_signals(df)

    a = pd.Timestamp("2026-01-01", tz="UTC")
    b = pd.Timestamp("2026-08-11", tz="UTC")
    win = sg[(sg.index >= a) & (sg.index < b)]
    n_sig = int((win["sinyal"] != 0).sum())

    print("=" * 88)
    print("VALIDASI PORT terhadap hasil MT5 Strategy Tester")
    print("=" * 88)
    print(f"  periode        : 2026-01-01 .. 2026-08-11, XAUUSD M15")
    print(f"  bar M15        : {len(win)}")
    print(f"  sinyal port    : {n_sig}   (BUY {int((win['sinyal']==1).sum())} / SELL {int((win['sinyal']==-1).sum())})")
    print(f"  seri MT5 (acuan): 49")
    selisih = abs(n_sig - 49) / 49 * 100 if n_sig else 100
    print(f"  selisih        : {selisih:.0f}%")
    print()
    if selisih <= 20:
        print("  >> PORT DITERIMA. Cukup dekat untuk dipakai MENCARI struktur TP/SL.")
        print("     Pemenangnya nanti WAJIB dikonfirmasi ulang di MT5 tester sebelum dipasang.")
    else:
        print("  >> PORT DITOLAK. Selisihnya terlalu besar - ada yang belum cocok dengan EA.")
        print("     JANGAN dipakai riset sampai sumber selisihnya ketemu.")
    print()
    print("  Catatan: kecocokan sempurna TIDAK diharapkan. EA berjalan per-TICK sementara")
    print("  port ini per-BAR, dan filter berita MT5 tidak ada di sini. Yang dicari adalah")
    print("  ORDE BESARAN yang sama, bukan angka identik.")

    if len(win):
        print("\n  10 sinyal pertama 2026:")
        for ts, r in win[win["sinyal"] != 0].head(10).iterrows():
            print(f"    {ts:%Y-%m-%d %H:%M} UTC  {'BUY ' if r['sinyal']==1 else 'SELL'}  "
                  f"close {r['close']:.2f}  smaNorm {r['smaNorm']:.1f}")


if __name__ == "__main__":
    main()
