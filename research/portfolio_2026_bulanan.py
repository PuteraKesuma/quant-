"""FASE-33: RINCIAN BULANAN 2026 - portofolio 4 sleeve, modal awal $1000.

Rasio lot NYATA yang dipasang (bukan bobot inverse-vol teoritis yang butuh lot pecahan):
    ORB    0.03   RSI2 0.02   ETERNA 0.01   ZREV 0.01

Dua versi dilaporkan, dan perbedaannya penting:
  IDEAL     - menganggap semua sleeve independen (cara backtest lama menghitung)
  REALISTIS - eterna DIBLOKIR saat zrev memegang arah yang sama, karena 920622 ada di
              governor.magics sementara 920627 tidak. Inilah yang benar-benar terjadi live.

Data lokal berhenti 25 Juni; disambung tarikan Dukascopy segar supaya sampai hari ini.

Jalankan: python research/portfolio_2026_bulanan.py
"""
import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from portfolio_final import sleeve_eterna, sleeve_rsi2
from portfolio_audit import zrev_dollars, nas_dollars

CAPITAL = 1000.0
LOTS = {"ORB_nas": 3, "RSI2_nas": 2, "ETERNA_xau": 1, "ZREV_xau": 1}
START = pd.Timestamp("2026-01-01", tz="UTC")
END = pd.Timestamp("2026-08-01", tz="UTC")


def main():
    print("Membangun sleeve ...", flush=True)
    raw = {"ORB_nas": nas_dollars(), "RSI2_nas": sleeve_rsi2(),
           "ETERNA_xau": sleeve_eterna(), "ZREV_xau": zrev_dollars()}
    for k in raw:
        if raw[k].index.tz is None:
            raw[k].index = raw[k].index.tz_localize("UTC")

    scaled = {k: v * LOTS[k] for k, v in raw.items()}
    mon = pd.DataFrame({k: v.resample("ME").sum() for k, v in scaled.items()}).fillna(0.0)
    t = mon[(mon.index >= START) & (mon.index < END)].copy()

    z, e = t["ZREV_xau"], t["ETERNA_xau"]
    sama = ((z > 0) & (e > 0)) | ((z < 0) & (e < 0))
    e_blk = e.copy(); e_blk[sama] = 0.0

    t["IDEAL"] = t[["ORB_nas", "RSI2_nas", "ETERNA_xau", "ZREV_xau"]].sum(axis=1)
    t["REALISTIS"] = t["ORB_nas"] + t["RSI2_nas"] + t["ZREV_xau"] + e_blk

    print("\n" + "=" * 108)
    print("RINCIAN BULANAN 2026 - modal awal $1.000, lot ORB 0.03 / RSI2 0.02 / ETERNA 0.01 / ZREV 0.01")
    print("=" * 108)
    hdr = f"{'Bulan':<10}{'ORB':>10}{'RSI2':>10}{'ETERNA':>10}{'ZREV':>10}{'TOTAL':>12}{'EQUITY':>12}{'DD%':>8}"
    print(hdr)
    print("-" * 108)
    eq = CAPITAL
    peak = CAPITAL
    for i, r in t.iterrows():
        eq += r["REALISTIS"]
        peak = max(peak, eq)
        dd = 100 * (eq - peak) / peak
        blk = " *" if sama.loc[i] else "  "
        print(f"{i:%b %Y}{'':<3}{r['ORB_nas']:>10.2f}{r['RSI2_nas']:>10.2f}"
              f"{r['ETERNA_xau']:>10.2f}{r['ZREV_xau']:>10.2f}"
              f"{r['REALISTIS']:>12.2f}{eq:>12.2f}{dd:>7.1f}%{blk}")
    print("-" * 108)
    print("  * = bulan di mana zrev & eterna searah -> eterna DIBLOKIR (kontribusinya 0)")

    tot = t["REALISTIS"].sum()
    print(f"\n{'RINGKASAN (versi REALISTIS - yang akan terjadi live)':<55}")
    print(f"  Modal awal        : ${CAPITAL:,.2f}")
    print(f"  Equity akhir      : ${CAPITAL + tot:,.2f}")
    print(f"  Net               : ${tot:+,.2f}  ({100*tot/CAPITAL:+.1f}%)")
    print(f"  Bulan hijau       : {int((t['REALISTIS']>0).sum())} dari {len(t)}")
    print(f"  Bulan terbaik     : ${t['REALISTIS'].max():+,.2f}")
    print(f"  Bulan terburuk    : ${t['REALISTIS'].min():+,.2f}")
    e2 = CAPITAL + t["REALISTIS"].cumsum()
    print(f"  maxDD             : {100*((e2-e2.cummax())/e2.cummax()).min():.1f}%")

    print(f"\n{'PEMBANDING (versi IDEAL - backtest lama, terlalu optimis)':<55}")
    ti = t["IDEAL"].sum()
    ei = CAPITAL + t["IDEAL"].cumsum()
    print(f"  Net ${ti:+,.2f} ({100*ti/CAPITAL:+.1f}%)   equity akhir ${CAPITAL+ti:,.2f}   "
          f"maxDD {100*((ei-ei.cummax())/ei.cummax()).min():.1f}%")
    print(f"  Selisih vs realistis: ${ti-tot:+,.2f} - inilah yang HILANG karena _book_conflict.")

    print(f"\n{'PER SLEEVE sepanjang Jan-Jul 2026':<55}")
    for c in ["ORB_nas", "RSI2_nas", "ETERNA_xau", "ZREV_xau"]:
        s = t[c]
        print(f"  {c:<12} net ${s.sum():>9.2f}   hijau {int((s>0).sum())}/{len(s)}   "
              f"terbaik ${s.max():>8.2f}   terburuk ${s.min():>8.2f}")

    t.round(2).to_csv(r"C:\Quant\_MONITOR\portfolio_2026_bulanan.csv")
    print("\nDisimpan: C:\\Quant\\_MONITOR\\portfolio_2026_bulanan.csv")


if __name__ == "__main__":
    main()
