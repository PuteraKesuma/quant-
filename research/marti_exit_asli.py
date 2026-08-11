"""KOREKSI atas research/marti_payoff_riset.py - uji ulang dengan STRUKTUR EXIT ASLI EA.

KESALAHANKU YANG DIPERBAIKI DI SINI (user benar menegur):
  1. Sapuan sebelumnya menguji SATU posisi dengan TP/SL TETAP. Itu bukan cara EA keluar.
     EA membuka DUA posisi dan keluar bertingkat: #1 di +$10, #2 lebih jauh, dengan
     SL KERANJANG bersama. Trailing dan tangga exit berperilaku beda dari TP tetap.
  2. Grid-nya TP 5..30 / SL 5..30. Titik kerja EA yang sebenarnya SL keranjang $70
     (= $35 per posisi saat dua-duanya terbuka) TIDAK PERNAH masuk grid. Aku menolak
     sebuah struktur tanpa pernah mengujinya.
  3. Aku memilih M15 sendiri tanpa bertanya. `_Period` EA ditentukan chart-nya; kalau
     user melihatnya untung, bisa jadi di timeframe lain sama sekali.

URUTAN EXIT SEBENARNYA (dibaca dari kode, bukan ditebak):
  OnTick  -> CheckGlobalTP_SL() DULU, baru ManageDualEntryPositions().
  Jadi prioritasnya:
    a. keranjang (jumlah floating dua posisi) >= +$25  -> TUTUP SEMUA
    b. keranjang <= -$70                                -> TUTUP SEMUA
    c. posisi #1 profit >= $10                          -> tutup #1 saja
    d. posisi #2 profit >= $25 -> trailing aktif, tutup kalau turun $2 dari puncak
  Catatan penting: karena (a) diperiksa lebih dulu dan keranjang mencapai +$25 saat
  emas bergerak +$12,5 (dua posisi 0.01), keranjang sering menutup SEBELUM #1 sempat
  kena TP $10-nya... TIDAK - #1 kena di emas +$10 saat keranjang baru +$20. Jadi
  tangganya: emas +$10 tutup #1, lalu #2 sendirian butuh +$25 untuk keranjang TP.

Simulasi ini berjalan di bar M1 supaya urutan sentuhan dalam bar jujur.
Sinyal dari research/marti_signal_port.py yang sudah divalidasi (47 vs 49 di 2026).

Jalankan: python research/marti_exit_asli.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import marti_signal_port as msp

SPREAD = 0.30          # per posisi, bolak-balik, pada 0.01 lot
CAPITAL = 1000.0


def load_m1() -> pd.DataFrame:
    con = duckdb.connect(str(ROOT / "data" / "Level_0_Raw" / "XAUUSD_1m.duckdb"), read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def to_tf(m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    return m1.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def simulasi_ea(m1: pd.DataFrame, sinyal: pd.Series, tf_menit: int,
                tp1=10.0, basket_tp=25.0, basket_sl=70.0,
                trail_start=25.0, trail_step=2.0, max_hari=10) -> pd.DataFrame:
    """Tiru tangga exit EA: 2 posisi 0.01, keranjang TP/SL, #1 TP tetap, #2 trailing."""
    o = m1["open"].to_numpy(); hi = m1["high"].to_numpy()
    lo = m1["low"].to_numpy(); cl = m1["close"].to_numpy()
    idx = m1.index
    pos_of = {t: i for i, t in enumerate(idx)}

    hasil = []
    for ts, arah in sinyal.items():
        t0 = ts + pd.Timedelta(minutes=tf_menit)
        i = pos_of.get(t0)
        if i is None:
            k = idx.searchsorted(t0)
            if k >= len(idx):
                continue
            i = int(k)
        entry = o[i]
        akhir = min(i + max_hari * 1440, len(m1) - 1)

        p1_open, p2_open = True, True
        trail_on, peak = False, 0.0
        pnl_total = 0.0
        sebab = "WAKTU"
        for j in range(i, akhir + 1):
            # gerak searah paling jauh & paling buruk di bar ini
            if arah == 1:
                baik = hi[j] - entry
                buruk = lo[j] - entry
            else:
                baik = entry - lo[j]
                buruk = entry - hi[j]

            n_open = int(p1_open) + int(p2_open)
            if n_open == 0:
                break

            # --- (b) SL keranjang lebih dulu: konservatif ---
            if buruk * n_open <= -basket_sl:
                pnl_total += buruk * n_open
                sebab = "SL_KERANJANG"
                p1_open = p2_open = False
                break

            # --- (a) TP keranjang ---
            if baik * n_open >= basket_tp:
                pnl_total += basket_tp
                sebab = "TP_KERANJANG"
                p1_open = p2_open = False
                break

            # --- (c) #1 TP tetap ---
            if p1_open and baik >= tp1:
                pnl_total += tp1
                p1_open = False

            # --- (d) #2 trailing ---
            if p2_open and not p1_open:
                if not trail_on and baik >= trail_start:
                    trail_on, peak = True, baik
                elif trail_on:
                    peak = max(peak, baik)
                    kini = (cl[j] - entry) * arah
                    if peak - kini >= trail_step:
                        pnl_total += kini
                        p2_open = False
                        sebab = "TRAIL"
                        break
        else:
            j = akhir

        if p1_open or p2_open:
            sisa = int(p1_open) + int(p2_open)
            pnl_total += (cl[j] - entry) * arah * sisa

        hasil.append((ts, arah, entry, pnl_total - SPREAD * 2, sebab))

    return pd.DataFrame(hasil, columns=["masuk", "arah", "px", "pnl", "sebab"])


def ringkas(t: pd.DataFrame, label: str) -> dict | None:
    if len(t) < 20:
        return None
    d = t.set_index("masuk").pnl
    eq = CAPITAL + d.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    m_, k_ = d[d > 0], d[d <= 0]
    pf = m_.sum() / -k_.sum() if len(k_) and k_.sum() < 0 else np.inf
    thn = d.groupby(d.index.year).sum()
    return {"setelan": label, "n": len(d), "net$": round(d.sum(), 2),
            "PF": round(pf, 2), "winrate%": round(100 * len(m_) / len(d)),
            "maxDD%": round(100 * dd, 1),
            "thn rugi": f"{int((thn < 0).sum())}/{len(thn)}"}


def main():
    print("Memuat data ...", flush=True)
    m1 = load_m1()

    print("\n" + "=" * 104)
    print("A. STRUKTUR EXIT ASLI EA, LINTAS TIMEFRAME  (2021-2026, modal $1000)")
    print("   TP1 $10 | keranjang TP $25 / SL $70 | trailing #2 dari $25 langkah $2")
    print("=" * 104)
    rows = []
    tfs = [("5min", 5), ("15min", 15), ("30min", 30), ("1h", 60), ("4h", 240)]
    sinyal_per_tf = {}
    for rule, menit in tfs:
        bars = to_tf(m1, rule)
        sg = msp.build_signals(bars)
        s = sg[sg["sinyal"] != 0]["sinyal"]
        sinyal_per_tf[rule] = (s, menit)
        r = ringkas(simulasi_ea(m1, s, menit), f"{rule:>6}  ({len(s)} sinyal)")
        if r:
            rows.append(r)
        print(f"  {rule:>6} selesai - {len(s)} sinyal", flush=True)
    print()
    print(pd.DataFrame(rows).to_string(index=False))

    # timeframe terbaik -> rincian per tahun
    best = max(rows, key=lambda r: r["net$"])
    rule = best["setelan"].split()[0]
    s, menit = sinyal_per_tf[rule]
    print("\n" + "=" * 104)
    print(f"B. TIMEFRAME TERBAIK ({rule}) - PER TAHUN")
    print("=" * 104)
    t = simulasi_ea(m1, s, menit)
    t["thn"] = t.masuk.dt.year
    print(f"  {'tahun':<8}{'n':>5}{'net$':>10}{'PF':>7}{'winrate':>10}")
    for y, g in t.groupby("thn"):
        m_, k_ = g.pnl[g.pnl > 0], g.pnl[g.pnl <= 0]
        pf = m_.sum() / -k_.sum() if len(k_) and k_.sum() < 0 else 99
        print(f"  {y:<8}{len(g):>5}{g.pnl.sum():>10.2f}{pf:>7.2f}{100*(g.pnl>0).mean():>9.0f}%")
    print(f"\n  sebab keluar: " + ", ".join(f"{k} {v}" for k, v in t.sebab.value_counts().items()))

    print("\n" + "=" * 104)
    print(f"C. SAPUAN SL KERANJANG di {rule} - rentang yang DULU TIDAK PERNAH kuuji")
    print("=" * 104)
    rows2 = []
    for bsl in (20, 35, 50, 70, 100, 150):
        for btp in (15, 25, 40):
            r = ringkas(simulasi_ea(m1, s, menit, basket_tp=btp, basket_sl=bsl),
                        f"keranjang TP ${btp} / SL ${bsl}")
            if r:
                rows2.append(r)
    print(pd.DataFrame(rows2).sort_values("net$", ascending=False).to_string(index=False))

    print("\n" + "=" * 104)
    print("Kalau ada baris yang untung DAN tahun ruginya sedikit, itu kandidat nyata -")
    print("dan wajib dikonfirmasi ulang di MT5 Strategy Tester sebelum dipasang.")
    print("=" * 104)


if __name__ == "__main__":
    main()
