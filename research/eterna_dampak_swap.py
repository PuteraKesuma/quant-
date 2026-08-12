"""DAMPAK SWAP pada eterna — biaya yang TIDAK PERNAH dimodelkan di satu pun backtest.

DITEMUKAN 2026-08-12 dari posisi live: eterna_xau BUY 0.01 lot menahan 17 jam dan
sudah kena swap -$2,10. Semua backtest di proyek ini (blocking_akurat, portfolio_*,
eterna_*) hanya memotong spread $0,50 per trade dan MENGABAIKAN swap sepenuhnya.

Itu bukan kelalaian kecil untuk strategi ini: eterna H1 dengan TP 1:4 menahan posisi
BERHARI-HARI. Biaya menginap menumpuk sementara backtest menganggapnya nol.

TARIF NYATA FBS-Demo (SYMBOL_SWAP_MODE_POINTS, diukur 2026-08-12):
    swap_long  = -69,95 poin  ->  0.01 lot = -$0,6995 per malam
    swap_short = +24,91 poin  ->  0.01 lot = +$0,2491 per malam
Rabu dikenakan TIGA KALI (terbukti: posisi live kena -$2,10 = -$0,70 x 3 di hari Rabu).

Asimetrinya penting: LONG membayar carry, SHORT justru MENERIMA. Jadi dampaknya
bergantung pada komposisi arah eterna, bukan sekadar lama tahan.

Skrip ini memakai port eterna yang SUDAH DIVALIDASI (584 trade, $2789,95) lalu
mengenakan swap per-arah per-malam, termasuk pengali Rabu.

Jalankan: python research/eterna_dampak_swap.py
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

SWAP_LONG = -0.6995        # $ per malam per 0.01 lot
SWAP_SHORT = +0.2491
CAPITAL = 548.19


def malam_kena_swap(masuk: pd.Timestamp, keluar: pd.Timestamp) -> float:
    """Jumlah 'malam' swap antara masuk dan keluar, Rabu dihitung 3x.

    MT5 mengenakan swap pada pergantian hari server. Rabu (weekday 2) dikenakan
    tiga kali untuk menutup akhir pekan. Akhir pekan sendiri tidak ada rollover.
    """
    total = 0.0
    d = masuk.normalize()
    while d < keluar.normalize():
        d = d + pd.Timedelta(days=1)
        if d > keluar.normalize():
            break
        wd = d.weekday()
        if wd == 5 or wd == 6:          # Sabtu/Minggu tidak ada rollover
            continue
        total += 3.0 if wd == 2 else 1.0   # Rabu 3x
    return total


def main():
    print("Membangun ...", flush=True)
    t = eterna_trades(load_h1())
    print(f"\n  VALIDASI: {len(t)} trade, net ${t.pnl.sum():.2f}  (acuan 584, $2789.95)")
    if abs(len(t) - 584) > 2:
        print("  >> tidak cocok, berhenti."); return

    t["jam"] = (t.keluar - t.masuk).dt.total_seconds() / 3600
    t["malam"] = [malam_kena_swap(a, b) for a, b in zip(t.masuk, t.keluar)]
    t["swap"] = np.where(t.arah == 1, t.malam * SWAP_LONG, t.malam * SWAP_SHORT)
    t["pnl_swap"] = t.pnl + t.swap

    print("\n" + "=" * 96)
    print("A. LAMA TAHAN — kenapa swap penting untuk strategi ini")
    print("=" * 96)
    print(f"  rata-rata {t.jam.mean():.1f} jam ({t.jam.mean()/24:.1f} hari)   "
          f"median {t.jam.median():.1f} jam   terlama {t.jam.max():.0f} jam")
    print(f"  rata-rata unit swap per trade: {t.malam.mean():.2f}   median {t.malam.median():.0f}")
    lon, sho = t[t.arah == 1], t[t.arah == -1]
    print(f"\n  LONG  {len(lon):>3} trade ({100*len(lon)/len(t):.0f}%)  "
          f"rata2 {lon.malam.mean():.2f} unit swap  -> biaya ${-lon.swap.sum():.2f}")
    print(f"  SHORT {len(sho):>3} trade ({100*len(sho)/len(t):.0f}%)  "
          f"rata2 {sho.malam.mean():.2f} unit swap  -> TERIMA ${sho.swap.sum():.2f}")

    print("\n" + "=" * 96)
    print("B. DAMPAK KE HASIL ETERNA")
    print("=" * 96)
    n0, n1 = t.pnl.sum(), t.pnl_swap.sum()
    w0, l0 = t.pnl[t.pnl > 0].sum(), -t.pnl[t.pnl < 0].sum()
    w1, l1 = t.pnl_swap[t.pnl_swap > 0].sum(), -t.pnl_swap[t.pnl_swap < 0].sum()
    print(f"  {'':<22}{'net$':>11}{'per trade':>12}{'PF':>8}")
    print(f"  {'TANPA swap (backtest)':<22}{n0:>11.2f}{t.pnl.mean():>12.3f}{w0/l0:>8.2f}")
    print(f"  {'DENGAN swap (nyata)':<22}{n1:>11.2f}{t.pnl_swap.mean():>12.3f}{w1/l1:>8.2f}")
    print(f"  {'selisih':<22}{n1-n0:>11.2f}{(n1-n0)/len(t):>12.3f}   "
          f"({100*(n1-n0)/abs(n0):+.1f}%)")

    print("\n" + "=" * 96)
    print("C. PER TAHUN")
    print("=" * 96)
    t["thn"] = t.masuk.dt.year
    print(f"  {'tahun':<8}{'tanpa swap':>13}{'dengan swap':>14}{'selisih':>11}")
    for y, g in t.groupby("thn"):
        print(f"  {y:<8}{g.pnl.sum():>13.2f}{g.pnl_swap.sum():>14.2f}{g.swap.sum():>11.2f}")

    print("\n" + "=" * 96)
    print("D. DAMPAK KE PORTOFOLIO (ORB 0.01 + ETERNA 0.01, modal $548)")
    print("=" * 96)
    orb = nas_dollars()
    if orb.index.tz is None:
        orb.index = orb.index.tz_localize("UTC")
    mo = orb.resample("ME").sum()

    def m_(pnl_series):
        j = pd.DataFrame({"ORB": mo, "ET": pnl_series.resample("ME").sum()}).fillna(0.0)
        j = j.loc[(j != 0).any(axis=1)]
        p = j["ORB"] + j["ET"]
        eq = CAPITAL + p.cumsum()
        dd = float(((eq - eq.cummax()) / eq.cummax()).min())
        yrs = len(p) / 12.0
        cagr = (eq.iloc[-1] / CAPITAL) ** (1 / yrs) - 1
        r = p / CAPITAL
        return {"CAGR%": round(100 * cagr, 1), "maxDD%": round(100 * dd, 1),
                "Calmar": round(cagr / abs(dd), 2),
                "Sharpe": round(r.mean() / r.std(ddof=1) * np.sqrt(12), 2),
                "hijau%": round(100 * (p > 0).mean())}

    a = m_(t.set_index("masuk").pnl)
    b = m_(t.set_index("masuk").pnl_swap)
    print(f"  {'':<24}{'CAGR%':>9}{'maxDD%':>9}{'Calmar':>9}{'Sharpe':>9}{'hijau%':>9}")
    print(f"  {'tanpa swap (dipercaya)':<24}{a['CAGR%']:>9}{a['maxDD%']:>9}"
          f"{a['Calmar']:>9}{a['Sharpe']:>9}{a['hijau%']:>9}")
    print(f"  {'dengan swap (nyata)':<24}{b['CAGR%']:>9}{b['maxDD%']:>9}"
          f"{b['Calmar']:>9}{b['Sharpe']:>9}{b['hijau%']:>9}")

    print("\n" + "=" * 96)


if __name__ == "__main__":
    main()
