"""FASE-32: 3 SLEEVE (tanpa ZREV) vs 4 SLEEVE (dengan ZREV) - pakai RASIO LOT NYATA.

User mematikan ZREV. Pertanyaannya: apakah ZREV benar-benar dibutuhkan?

Dihitung dengan lot yang BENAR-BENAR dipasang, bukan bobot inverse-vol teoritis:
    3 sleeve : ORB 0.03 | RSI2 0.02 | ETERNA 0.01          (rasio 3:2:1)
    4 sleeve : ORB 0.03 | RSI2 0.02 | ETERNA 0.01 | ZREV 0.01  (rasio 3:2:1:1)

SATU KOREKSI PENTING yang belum pernah dimasukkan ke backtest mana pun:
    ZREV (920622) ADA di governor.magics, ETERNA (920627) TIDAK. Jadi di LIVE,
    _book_conflict() MEMBLOKIR eterna setiap kali zrev sudah memegang XAUUSD searah.
    Backtest 4-sleeve sebelumnya menganggap keduanya independen -> TERLALU OPTIMIS.
    Skrip ini juga menghitung versi "4 sleeve REALISTIS" di mana entry eterna DIBUANG
    kalau zrev sedang memegang arah yang sama pada saat itu.

Jalankan: python research/portfolio_3vs4.py
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
LOTS = {"ORB_nas": 3, "RSI2_nas": 2, "ETERNA_xau": 1, "ZREV_xau": 1}   # unit 0.01


def metrics(m, label):
    m = m.dropna()
    if len(m) < 12:
        return None
    eq = CAPITAL + m.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min()
    yrs = len(m) / 12.0
    cagr = (eq.iloc[-1] / CAPITAL) ** (1 / yrs) - 1
    mr = m / CAPITAL
    sh = mr.mean() / mr.std(ddof=1) * np.sqrt(12) if mr.std(ddof=1) > 0 else np.nan
    st_ = mx = 0
    for v in m:
        st_ = st_ + 1 if v < 0 else 0
        mx = max(mx, st_)
    return {"portofolio": label, "bln": len(m), "CAGR%": round(100 * cagr, 1),
            "maxDD%": round(100 * dd, 1), "Sharpe": round(sh, 2),
            "Calmar": round(cagr / abs(dd), 2) if dd else np.nan,
            "hijau%": round(100 * (m > 0).mean()), "beruntun": mx,
            "terburuk$": round(m.min())}


def main():
    print("Membangun sleeve ...", flush=True)
    raw = {"ORB_nas": nas_dollars(), "RSI2_nas": sleeve_rsi2(),
           "ETERNA_xau": sleeve_eterna(), "ZREV_xau": zrev_dollars()}
    for k in raw:
        if raw[k].index.tz is None:
            raw[k].index = raw[k].index.tz_localize("UTC")
        print(f"  {k:<12} {len(raw[k]):>5} trade", flush=True)

    # skala ke lot yang benar-benar dipasang (semua sleeve di-backtest pada 0.01)
    scaled = {k: v * LOTS[k] for k, v in raw.items()}
    mon = pd.DataFrame({k: v.resample("ME").sum() for k, v in scaled.items()}).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]

    p3 = mon[["ORB_nas", "RSI2_nas", "ETERNA_xau"]].sum(axis=1)
    p4 = mon[["ORB_nas", "RSI2_nas", "ETERNA_xau", "ZREV_xau"]].sum(axis=1)

    print("\n" + "=" * 104)
    print("A. TIAP SLEEVE pada LOT YANG DIPASANG")
    print("=" * 104)
    rows = [metrics(mon[c], f"{c} (lot 0.0{LOTS[c]})") for c in mon.columns]
    print(pd.DataFrame([r for r in rows if r]).to_string(index=False))

    print("\n" + "=" * 104)
    print("B. 3 SLEEVE vs 4 SLEEVE")
    print("=" * 104)
    rows = [metrics(p3, "3 sleeve (TANPA zrev)  ORB+RSI2+ETERNA"),
            metrics(p4, "4 sleeve (DENGAN zrev) +ZREV")]
    print(pd.DataFrame([r for r in rows if r]).to_string(index=False))

    print("\n" + "=" * 104)
    print("C. KOREKSI REALISTIS - efek _book_conflict yang memblokir eterna")
    print("=" * 104)
    # Perkiraan konservatif: buang bulan-bulan di mana ZREV dan ETERNA sama-sama untung
    # ATAU sama-sama rugi besar (indikasi mereka memegang arah yang sama sepanjang bulan).
    # Di live, eterna akan DIBLOKIR pada periode seperti itu -> kontribusinya hilang.
    z, e = mon["ZREV_xau"], mon["ETERNA_xau"]
    sama_arah = ((z > 0) & (e > 0)) | ((z < 0) & (e < 0))
    e_blocked = e.copy()
    e_blocked[sama_arah] = 0.0            # eterna diblokir -> tak menyumbang apa pun
    p4r = mon["ORB_nas"] + mon["RSI2_nas"] + z + e_blocked
    print(f"  bulan di mana zrev & eterna searah (eterna kemungkinan diblokir): "
          f"{int(sama_arah.sum())} dari {len(mon)} ({100*sama_arah.mean():.0f}%)")
    rows = [metrics(p3, "3 sleeve (TANPA zrev)"),
            metrics(p4, "4 sleeve IDEAL (backtest lama, terlalu optimis)"),
            metrics(p4r, "4 sleeve REALISTIS (eterna diblokir saat searah zrev)")]
    print("\n" + pd.DataFrame([r for r in rows if r]).to_string(index=False))

    print("\n" + "=" * 104)
    print("D. KORELASI")
    print("=" * 104)
    print(mon.corr().round(2).to_string())

    print("\n" + "=" * 104)
    print("VONIS")
    print("=" * 104)
    m3, m4, m4r = metrics(p3, "3"), metrics(p4, "4"), metrics(p4r, "4r")
    print(f"  3 sleeve            : CAGR {m3['CAGR%']:>5.1f}%  maxDD {m3['maxDD%']:>6.1f}%  "
          f"Calmar {m3['Calmar']:>4.2f}  hijau {m3['hijau%']}%  beruntun {m3['beruntun']}")
    print(f"  4 sleeve REALISTIS  : CAGR {m4r['CAGR%']:>5.1f}%  maxDD {m4r['maxDD%']:>6.1f}%  "
          f"Calmar {m4r['Calmar']:>4.2f}  hijau {m4r['hijau%']}%  beruntun {m4r['beruntun']}")
    better = m4r["Calmar"] > m3["Calmar"] * 1.10
    print(f"\n  >> {'ZREV LAYAK dipasang lagi' if better else 'ZREV TIDAK memberi perbaikan berarti - biarkan MATI'}")
    print("     (syarat: Calmar 4-sleeve realistis harus >10% lebih baik dari 3-sleeve)")


if __name__ == "__main__":
    main()
