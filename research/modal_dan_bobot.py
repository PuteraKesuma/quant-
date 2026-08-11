"""Berapa modal yang dibutuhkan supaya bobot portofolio bisa dipasang DENGAN BENAR.

Ini perhitungan MEKANIS, bukan pencarian. Tidak ada parameter yang disapu, tidak ada
percobaan yang ditambahkan, jadi tidak menaikkan N maupun menurunkan Deflated Sharpe.
Yang dijawab: pada modal berapa granularitas lot 0.01 berhenti merusak bobot.

DUDUK PERKARANYA:
Lot minimum broker 0.01 dan kelipatannya 0.01. Di modal $548, portofolio ini memasang
ORB 0.03 + ETERNA 0.01 - rasio 3:1. Itu PEMBULATAN KASAR dari bobot inverse-vol yang
sebenarnya. Makin sedikit total unit lot, makin besar galat pembulatannya.

  4 unit total  -> tiap unit = 25% bobot; galat pembulatan bisa +/-12,5%
 40 unit total  -> tiap unit = 2,5%;  galat pembulatan +/-1,25%

Kenaikan modal TIDAK menaikkan return persen dengan sendirinya - kalau lot naik sebanding
modal, maxDD dalam PERSEN tetap sama. Yang membaik adalah KETEPATAN BOBOT, dan itulah
sumber perbaikannya. Skrip ini memisahkan dua hal itu supaya tidak tertukar.

Jalankan: python research/modal_dan_bobot.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from blocking_akurat import load_h1, eterna_trades
from portfolio_audit import nas_dollars

MIN_LOT, STEP_LOT = 0.01, 0.01


def bulan(s: pd.Series, unit: float) -> pd.Series:
    s = s.copy()
    if s.index.tz is None:
        s.index = s.index.tz_localize("UTC")
    return (s * unit).resample("ME").sum()


def metrik(m: pd.Series, modal: float) -> dict:
    eq = modal + m.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yrs = len(m) / 12.0
    cagr = (eq.iloc[-1] / modal) ** (1 / yrs) - 1
    r = m / modal
    sh = r.mean() / r.std(ddof=1) * np.sqrt(12)
    return {"CAGR%": 100 * cagr, "maxDD%": 100 * dd, "Sharpe": sh,
            "Calmar": cagr / abs(dd) if dd else np.nan}


def main():
    print("Membangun sleeve ...", flush=True)
    et = eterna_trades(load_h1()).set_index("masuk").pnl        # 0.01 lot
    orb = nas_dollars()                                          # 0.01 lot
    if orb.index.tz is None:
        orb.index = orb.index.tz_localize("UTC")

    m_orb = bulan(orb, 1.0)
    m_et = bulan(et, 1.0)
    mon = pd.DataFrame({"ORB": m_orb, "ETERNA": m_et}).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]

    v_orb = mon["ORB"].std(ddof=1)
    v_et = mon["ETERNA"].std(ddof=1)
    korel = mon["ORB"].corr(mon["ETERNA"])

    print("\n" + "=" * 96)
    print("A. BOBOT YANG SEHARUSNYA (inverse-vol) vs YANG TERPASANG")
    print("=" * 96)
    print(f"  volatilitas bulanan pada 0.01 lot:  ORB ${v_orb:.2f}   ETERNA ${v_et:.2f}")
    print(f"  korelasi bulanan                 :  {korel:+.3f}")
    iv_orb = (1 / v_orb) / (1 / v_orb + 1 / v_et)
    iv_et = 1 - iv_orb
    print(f"\n  bobot inverse-vol SEHARUSNYA     :  ORB {100*iv_orb:.1f}%   ETERNA {100*iv_et:.1f}%")
    w_orb = 3 / 4; w_et = 1 / 4
    print(f"  bobot TERPASANG (lot 0.03 : 0.01):  ORB {100*w_orb:.1f}%   ETERNA {100*w_et:.1f}%")
    print(f"  galat                            :  ORB {100*(w_orb-iv_orb):+.1f} poin   "
          f"ETERNA {100*(w_et-iv_et):+.1f} poin")

    print("\n" + "=" * 96)
    print("B. RISIKO PER TRADE PADA LOT SEKARANG - kenapa akun $548 terasa sesak")
    print("=" * 96)
    kalah_orb = -orb[orb < 0]
    print(f"  ORB 0.03  : rugi per trade median ${3*kalah_orb.median():.2f}   "
          f"persentil-90 ${3*kalah_orb.quantile(0.9):.2f}   terburuk ${3*kalah_orb.max():.2f}")
    kalah_et = -et[et < 0]
    print(f"  ETERNA 0.01: rugi per trade median ${kalah_et.median():.2f}   "
          f"persentil-90 ${kalah_et.quantile(0.9):.2f}   terburuk ${kalah_et.max():.2f}")
    print(f"\n  Di modal $548, satu trade ORB persentil-90 memakan "
          f"{100*3*kalah_orb.quantile(0.9)/548:.1f}% akun.")

    print("\n" + "=" * 96)
    print("C. MODAL vs KETEPATAN BOBOT")
    print("=" * 96)
    print("  Untuk tiap modal: skala lot supaya risiko sebanding, lalu BULATKAN ke 0.01.")
    print("  Target: maxDD portofolio sekitar -12% (target user 10-12%).\n")
    print(f"  {'modal $':>9}{'lot ORB':>10}{'lot ETRN':>10}{'unit':>7}"
          f"{'bobot ORB':>11}{'galat':>8}{'CAGR%':>8}{'maxDD%':>9}{'Calmar':>8}")
    print("  " + "-" * 82)

    # cari skala dasar: pada modal acuan, berapa lot supaya maxDD ~ -12%?
    # dasar = 1 unit (0.01) tiap sleeve pada bobot inverse-vol, lalu diskalakan.
    hasil = []
    for modal in (548, 1000, 2000, 3000, 5000, 10000, 20000):
        # unit total yang mampu ditanggung: skalakan linier terhadap modal,
        # dikalibrasi supaya modal 1000 -> sekitar 4 unit (yang terpasang sekarang)
        unit_total = max(2, round(4 * modal / 1000))
        u_orb = max(1, round(unit_total * iv_orb))
        u_et = max(1, unit_total - u_orb)
        lot_orb = u_orb * STEP_LOT
        lot_et = u_et * STEP_LOT
        w = u_orb / (u_orb + u_et)
        p = mon["ORB"] * u_orb + mon["ETERNA"] * u_et
        mt = metrik(p, modal)
        hasil.append((modal, lot_orb, lot_et, u_orb + u_et, w, mt))
        print(f"  {modal:>9,}{lot_orb:>10.2f}{lot_et:>10.2f}{u_orb+u_et:>7}"
              f"{100*w:>10.1f}%{100*(w-iv_orb):>+7.1f}{mt['CAGR%']:>8.1f}"
              f"{mt['maxDD%']:>9.1f}{mt['Calmar']:>8.2f}")

    print("\n  Perhatikan: CAGR% dan maxDD% nyaris TIDAK berubah antar baris.")
    print("  Itu memang seharusnya - kalau lot naik sebanding modal, hasil PERSEN tetap.")
    print("  Yang membaik hanya kolom 'galat': bobot makin mendekati yang seharusnya.")

    print("\n" + "=" * 96)
    print("D. JADI APA SEBENARNYA YANG DIBELI OLEH MODAL LEBIH BESAR")
    print("=" * 96)
    g548 = abs(100 * (3/4 - iv_orb))
    for modal in (548, 2000, 5000, 10000):
        unit_total = max(2, round(4 * modal / 1000))
        u_orb = max(1, round(unit_total * iv_orb))
        u_et = max(1, unit_total - u_orb)
        w = u_orb / (u_orb + u_et)
        print(f"  modal ${modal:>6,}  ->  {u_orb+u_et:>2} unit lot, galat bobot "
              f"{abs(100*(w-iv_orb)):>4.1f} poin persen")
    print(f"\n  Di $548 galatnya {g548:.1f} poin - bobot terpasang jauh dari yang seharusnya.")
    print("  Perbaikan itu NYATA tapi TERBATAS: dia merapikan alokasi, bukan menciptakan edge.")

    print("\n" + "=" * 96)
    print("E. YANG JAUH LEBIH BESAR PENGARUHNYA: SLEEVE KETIGA")
    print("=" * 96)
    print("  Sharpe portofolio naik seakar jumlah edge yang tidak berkorelasi.")
    sh2 = metrik(mon["ORB"] * 3 + mon["ETERNA"] * 1, 1000.0)["Sharpe"]
    print(f"    2 sleeve sekarang            Sharpe {sh2:.2f}")
    print(f"    3 sleeve setara & tak korelasi  ~Sharpe {sh2*np.sqrt(3/2):.2f}  (+{100*(np.sqrt(3/2)-1):.0f}%)")
    print(f"    4 sleeve                        ~Sharpe {sh2*np.sqrt(4/2):.2f}  (+{100*(np.sqrt(2)-1):.0f}%)")
    print("\n  Menambah SATU edge nyata yang tak berkorelasi mengalahkan perapian bobot")
    print("  maupun penambahan modal. Masalahnya: edge itu harus benar-benar ada -")
    print("  hari ini tiga kandidat gagal (EA martingale, TP/SL eterna, konfirmasi S/R).")

    print("\n" + "=" * 96)


if __name__ == "__main__":
    main()
