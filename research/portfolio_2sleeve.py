"""PORTOFOLIO 2 SLEEVE: ORB (US100) + ETERNA (XAU). Sisanya sudah dimatikan.

Riwayat keputusan (semuanya 2026-08-11, semuanya berdasar audit, bukan selera):
  ZREV dimatikan - sumbangan BERSIH -$742 setelah biaya memblokir 53,4% entry eterna
                   (research/portfolio_3vs4_akurat.py)
  RSI2 dimatikan - backtest dan kode live BUKAN strategi yang sama; perkiraan live
                   -81% dari angka backtest, dan bug re-entry menggandakan drawdown
                   (research/audit_orb_rsi2.py, audit_rsi2_stop.py)

YANG HARUS DIHADAPI DENGAN JUJUR DI SINI:
Membuang sleeve yang buruk itu benar. Tapi RSI2 adalah satu-satunya sleeve
MEAN-REVERSION di buku - korelasinya ~0,04 terhadap ORB dan ~0,04 terhadap eterna.
Menghapusnya berarti portofolio tinggal DUA aliran, dan diversifikasi adalah
satu-satunya makan siang gratis yang tersedia. Skrip ini mengukur harga itu, tidak
menyembunyikannya.

Juga diuji: apakah eterna pantas naik dari 0.01 ke 0.02 sekarang setelah cuma
berdua dengan ORB.

Jalankan: python research/portfolio_2sleeve.py
"""
import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))

from blocking_akurat import load_h1, eterna_trades
from portfolio_audit import nas_dollars

CAPITAL = 1000.0


def dsr(r, n_trials):
    r = np.asarray(r, float); n = len(r)
    if n < 12 or r.std(ddof=1) == 0:
        return np.nan, np.nan, np.nan
    sr = r.mean() / r.std(ddof=1)
    sk, ku = stats.skew(r), stats.kurtosis(r, fisher=False)
    e = np.euler_gamma
    sr0 = np.sqrt(1.0 / (n - 1)) * ((1 - e) * stats.norm.ppf(1 - 1.0 / n_trials)
                                    + e * stats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    den = np.sqrt(1 - sk * sr + (ku - 1) / 4.0 * sr ** 2)
    if den <= 0 or np.isnan(den):
        return sr, sr0, np.nan
    return sr, sr0, stats.norm.cdf((sr - sr0) * np.sqrt(n - 1) / den)


def metrik(m, label):
    m = m.dropna()
    eq = CAPITAL + m.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yrs = len(m) / 12.0
    cagr = (eq.iloc[-1] / CAPITAL) ** (1 / yrs) - 1
    mr = m / CAPITAL
    sh = mr.mean() / mr.std(ddof=1) * np.sqrt(12)
    st = mx = 0
    for v in m:
        st = st + 1 if v < 0 else 0
        mx = max(mx, st)
    return {"portofolio": label, "CAGR%": round(100 * cagr, 1), "maxDD%": round(100 * dd, 1),
            "Calmar": round(cagr / abs(dd), 2), "Sharpe": round(sh, 2),
            "hijau%": round(100 * (m > 0).mean()), "merah beruntun": mx,
            "bulan terburuk$": round(m.min()), "equity akhir$": round(eq.iloc[-1])}


def main():
    print("Membangun sleeve ...", flush=True)
    et = eterna_trades(load_h1()).set_index("masuk").pnl      # tanpa zrev -> tidak diblokir
    orb = nas_dollars()
    if orb.index.tz is None:
        orb.index = orb.index.tz_localize("UTC")

    def bln(s, unit):
        s = s.copy()
        if s.index.tz is None:
            s.index = s.index.tz_localize("UTC")
        return (s * unit).resample("ME").sum()

    mon = pd.DataFrame({"ORB3": bln(orb, 3), "ET1": bln(et, 1), "ET2": bln(et, 2)}).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]

    p21 = mon["ORB3"] + mon["ET1"]        # yang dipasang sekarang: 0.03 + 0.01
    p22 = mon["ORB3"] + mon["ET2"]        # eterna naik ke 0.02
    solo_orb = mon["ORB3"]
    solo_et = mon["ET1"]

    print("\n" + "=" * 104)
    print("A. YANG DIPASANG SEKARANG vs ALTERNATIFNYA")
    print("=" * 104)
    rows = [metrik(solo_orb, "ORB sendirian          0.03"),
            metrik(solo_et,  "ETERNA sendirian       0.01"),
            metrik(p21,      "2 sleeve  ORB 0.03 + ETERNA 0.01   <- terpasang"),
            metrik(p22,      "2 sleeve  ORB 0.03 + ETERNA 0.02")]
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n" + "=" * 104)
    print("B. HARGA KEHILANGAN DIVERSIFIKASI")
    print("=" * 104)
    print(f"  korelasi bulanan ORB vs ETERNA : {mon['ORB3'].corr(mon['ET1']):+.3f}")
    dd_jumlah = abs(metrik(solo_orb, "")["maxDD%"]) + abs(metrik(solo_et, "")["maxDD%"])
    dd_gabung = abs(metrik(p21, "")["maxDD%"])
    print(f"  maxDD kalau dijumlah begitu saja: {dd_jumlah:.1f}%")
    print(f"  maxDD gabungan sebenarnya       : {dd_gabung:.1f}%")
    print(f"  -> diversifikasi masih memotong {dd_jumlah - dd_gabung:.1f} poin persen.")
    print("     Dua aliran tetap lebih baik dari satu, tapi jauh lebih rapuh dari tiga:")
    print("     satu sleeve rusak sekarang berarti SETENGAH portofolio, bukan sepertiga.")

    print("\n" + "=" * 104)
    print("C. DEFLATED SHARPE - seberapa mungkin ini cuma hasil pencarian")
    print("=" * 104)
    for nm, s in (("ORB sendirian", solo_orb), ("ETERNA sendirian", solo_et),
                  ("2 sleeve terpasang", p21)):
        baris = []
        for n in (1, 20, 100, 500, 1900):
            sr, sr0, p = dsr(s / CAPITAL, n)
            baris.append(f"N={n}:{p:.3f}")
        print(f"  {nm:<22} " + "   ".join(baris))
    print("\n  Eterna dicari lewat ~1900 percobaan (24 fase riset), jadi kolom N=1900 yang")
    print("  berlaku untuknya. ORB parameternya sedikit - N=20..100 lebih masuk akal.")

    print("\n" + "=" * 104)
    print("D. PER TAHUN (2 sleeve terpasang)")
    print("=" * 104)
    print(f"  {'tahun':<8}{'ORB':>11}{'ETERNA':>11}{'TOTAL':>11}{'equity':>11}")
    eq = CAPITAL
    for y, g in p21.groupby(p21.index.year):
        o = mon.loc[g.index, "ORB3"].sum(); e = mon.loc[g.index, "ET1"].sum()
        eq += g.sum()
        print(f"  {y:<8}{o:>11.2f}{e:>11.2f}{g.sum():>11.2f}{eq:>11.2f}")

    print("\n" + "=" * 104)
    print("E. BULAN TERBURUK - yang harus kamu siap tahan")
    print("=" * 104)
    for ts, v in p21.nsmallest(5).items():
        print(f"  {ts:%b %Y}  ${v:+9.2f}   ({100*v/CAPITAL:+.1f}% dari modal awal)")

    print("\n" + "=" * 104)


if __name__ == "__main__":
    main()
