"""EDGE HUNT diulang dengan BIAYA NYATA per simbol — memperbaiki kesalahan metode.

KESALAHAN YANG DIPERBAIKI (user yang menemukannya):
edge_hunt_4keluarga.py membebankan biaya seragam 0,03 x ATR harian untuk semua simbol.
Terdengar netral, ternyata sangat bias. Dibandingkan spread FBS yang sebenarnya:

    simbol    biaya model   spread nyata   kelebihan
    XAUUSD       $2.627        $0.390        6,7x
    US100        $1.965        $0.230        8,5x
    EURUSD       $0.155        $0.090        1,7x
    AUDUSD       $0.134        $0.100        1,3x

Emas dan indeks dihukum 6-8x lipat sementara FX cuma 1,3-1,7x. Arah biasnya PERSIS
kebalikan dari kesimpulan yang diambil ("FX menang, XAU kalah"). Hasil lama tidak sah.

KESALAHAN KEDUA, dan ini lebih penting untuk akun kecil:
Hasil dilaporkan dalam satuan ATR. Itu adil secara statistik tapi menyembunyikan bahwa
pada lot 0.01 yang sama, emas bergerak $87,57 sehari sementara EURUSD $5,16 - selisih
17 KALI. Edge FX sebagus apa pun menghasilkan recehan di akun $548 dengan lot minimum
0.01. Sekarang semuanya dilaporkan dalam DOLAR pada 0.01 lot.

BIAYA YANG DIPAKAI SEKARANG (diukur dari FBS-Demo 2026-08-11, bukan ditebak):
  strategi biasa : 1,5 x spread  (spread + kelonggaran slippage)
  strategi berita: 3,0 x spread  (spread MELEBAR tajam saat rilis 8:30 ET - kalau
                   tidak dihitung, H4 akan terlihat jauh lebih bagus dari kenyataan)

Kriteria lulus SAMA dengan sebelumnya, tidak diubah:
  1. untung di >= 6 dari 9 pasar
  2. untung di >= 4 dari 6 tahun (basket)
  3. |korelasi| ke ORB & ETERNA < 0,30
  4. PF basket >= 1,15

Jalankan: python research/edge_hunt_biaya_nyata.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import edge_hunt_4keluarga as EH

# ---- diukur dari FBS-Demo 2026-08-11 ----
# (usd_per_1.0_gerak_harga @0.01 lot, spread dalam SATUAN HARGA)
# Ukuran poin beda-beda per simbol (XAU 2-digit, FX 5-digit, JPY 3-digit), jadi semuanya
# dinormalkan ke SATUAN HARGA supaya tidak ada lagi kekeliruan konversi.
# Verifikasi: usd_per_harga * spread_harga harus sama dengan kolom "spread $" yang diukur.
PASAR = {
    "XAUUSD": (1.0,     0.39),      # $1/$1 gerak; spread 39 poin = $0.39
    "NAS100": (0.10,    2.30),      # broker US100; $0.10 per 1 poin indeks; spread $0.23
    "EURUSD": (1000.0,  0.00009),   # $0.09
    "GBPUSD": (1000.0,  0.00012),   # $0.12
    "USDJPY": (6.30,    0.010),     # $0.063
    "USDCHF": (1234.0,  0.00009),   # $0.111
    "AUDUSD": (1000.0,  0.00010),   # $0.10
    "USDCAD": (718.0,   0.00011),   # $0.079
    "NZDUSD": (1000.0,  0.00012),   # $0.12
}
CAPITAL = 548.19
N_HIPOTESIS = 4


def biaya_usd(sym: str, berita: bool) -> float:
    usd_per_harga, spread_harga = PASAR[sym]
    return spread_harga * usd_per_harga * (3.0 if berita else 1.5)


def harga_ke_usd(sym: str) -> float:
    return PASAR[sym][0]


def dsr(r, n_trials):
    r = np.asarray(r, float); n = len(r)
    if n < 12 or r.std(ddof=1) == 0:
        return np.nan
    sr = r.mean() / r.std(ddof=1)
    sk, ku = stats.skew(r), stats.kurtosis(r, fisher=False)
    e = np.euler_gamma
    sr0 = np.sqrt(1.0 / (n - 1)) * ((1 - e) * stats.norm.ppf(1 - 1.0 / n_trials)
                                    + e * stats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    den = np.sqrt(1 - sk * sr + (ku - 1) / 4.0 * sr ** 2)
    if den <= 0 or np.isnan(den):
        return np.nan
    return float(stats.norm.cdf((sr - sr0) * np.sqrt(n - 1) / den))


def main():
    # matikan biaya internal modul lama; biaya dikenakan di sini dalam dolar
    EH.BIAYA_ATR = 0.0

    H = {"H1 acceleration": (EH.h1_trades, False),
         "H2 mean-rev intraday": (EH.h2_mr_intraday, False),
         "H3 london open": (EH.h3_london, False),
         "H4 news 8:30 ET": (EH.h4_news, True)}

    hasil = {k: {} for k in H}
    for sym in EH.SIMBOL:
        print(f"  {sym} ...", end="", flush=True)
        m1 = EH.load(sym)
        atr_d = EH.atr_harian(m1)
        # skrip lama mengembalikan hasil dalam satuan ATR; kalikan balik jadi POIN,
        # lalu ke DOLAR, lalu potong biaya nyata. atr_d sudah dalam satuan harga.
        for nama, (fn, brt) in H.items():
            s_atr = fn(m1, atr_d)
            if len(s_atr) == 0:
                hasil[nama][sym] = pd.Series(dtype=float)
                continue
            a = atr_d.reindex(s_atr.index, method="ffill")
            harga = s_atr * a                      # satuan ATR -> satuan HARGA
            usd = harga * harga_ke_usd(sym)        # satuan harga -> DOLAR @0.01 lot
            hasil[nama][sym] = (usd - biaya_usd(sym, brt)).dropna()
        print(" ok", flush=True)

    print("\n" + "=" * 108)
    print("A. HASIL DALAM DOLAR pada 0.01 lot, biaya nyata FBS sudah dipotong")
    print("=" * 108)
    ringkas = {}
    for nama in H:
        print(f"\n  {nama}   (biaya {'3x' if H[nama][1] else '1.5x'} spread)")
        print(f"    {'simbol':<10}{'n':>7}{'net $':>12}{'per trade':>12}{'PF':>7}")
        untung = 0
        for sym in EH.SIMBOL:
            s = hasil[nama][sym]
            if len(s) < 30:
                print(f"    {sym:<10}{len(s):>7}   (sedikit)")
                continue
            w, l = s[s > 0].sum(), -s[s < 0].sum()
            pf = w / l if l > 0 else 99
            if s.sum() > 0:
                untung += 1
            print(f"    {sym:<10}{len(s):>7}{s.sum():>12.2f}{s.mean():>12.3f}{pf:>7.2f}"
                  f"  {'+' if s.sum() > 0 else ''}")
        ringkas[nama] = untung
        print(f"    -> untung di {untung} dari 9 pasar")

    print("\n" + "=" * 108)
    print("B. VONIS terhadap kriteria yang TIDAK diubah")
    print("=" * 108)
    print(f"  {'hipotesis':<24}{'n':>7}{'net $':>11}{'PF':>7}{'thn+':>7}{'pasar+':>8}{'DSR':>8}   vonis")
    basket = {}
    for nama in H:
        semua = pd.concat([hasil[nama][s] for s in EH.SIMBOL]).sort_index()
        basket[nama] = semua
        w, l = semua[semua > 0].sum(), -semua[semua < 0].sum()
        pf = w / l if l > 0 else 99
        thn = semua.groupby(semua.index.year).sum()
        tp = int((thn > 0).sum())
        d = dsr(semua.resample("ME").sum(), N_HIPOTESIS)
        lulus = ringkas[nama] >= 6 and tp >= 4 and pf >= 1.15
        gag = []
        if ringkas[nama] < 6: gag.append(f"pasar {ringkas[nama]}/9")
        if tp < 4: gag.append(f"tahun {tp}/{len(thn)}")
        if pf < 1.15: gag.append(f"PF {pf:.2f}")
        print(f"  {nama:<24}{len(semua):>7}{semua.sum():>11.2f}{pf:>7.2f}"
              f"{tp:>5}/{len(thn)}{ringkas[nama]:>7}/9{d if d==d else 0:>8.3f}   "
              f"{'LULUS' if lulus else 'gagal: ' + ', '.join(gag)}")

    print("\n" + "=" * 108)
    print("C. KHUSUS XAU — apakah hasilnya berubah setelah biaya diperbaiki?")
    print("=" * 108)
    print(f"  {'hipotesis':<24}{'n':>7}{'net $':>11}{'per trade':>12}{'PF':>7}{'thn untung':>13}")
    for nama in H:
        s = hasil[nama]["XAUUSD"]
        if len(s) < 30:
            continue
        w, l = s[s > 0].sum(), -s[s < 0].sum()
        thn = s.groupby(s.index.year).sum()
        print(f"  {nama:<24}{len(s):>7}{s.sum():>11.2f}{s.mean():>12.3f}"
              f"{(w/l if l else 99):>7.2f}{int((thn>0).sum()):>10}/{len(thn)}")
    print("\n  Pembanding hasil LAMA (biaya salah, 6,7x kelebihan di XAU):")
    print("    H1 +3.99 ATR | H2 -106.50 | H3 -0.83 | H4 -4.20  -> semuanya 'mati'")

    print("\n" + "=" * 108)
    print("D. KORELASI ke sleeve yang ada")
    print("=" * 108)
    from blocking_akurat import load_h1, eterna_trades
    from portfolio_audit import nas_dollars
    et = eterna_trades(load_h1()).set_index("masuk").pnl
    orb = nas_dollars()
    if orb.index.tz is None:
        orb.index = orb.index.tz_localize("UTC")
    ref = pd.DataFrame({"ORB": orb.resample("ME").sum(),
                        "ETERNA": et.resample("ME").sum()}).fillna(0.0)
    for nama in H:
        b = basket[nama].resample("ME").sum()
        j = pd.concat([ref, b.rename("baru")], axis=1).fillna(0.0)
        print(f"  {nama:<24} vs ORB {j['ORB'].corr(j['baru']):+.3f}   "
              f"vs ETERNA {j['ETERNA'].corr(j['baru']):+.3f}")

    print("\n" + "=" * 108)


if __name__ == "__main__":
    main()
