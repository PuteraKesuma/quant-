"""FASE-31: BACKTEST PORTOFOLIO Jan-Jul 2026, out-of-sample sejati.

Bobot inverse-volatility dihitung HANYA dari 2021-2025, lalu diterapkan apa adanya ke
2026. Kalau bobot dihitung memakai data 2026, itu lookahead dan angkanya tidak berarti.

Dua versi dilaporkan:
  (a) TEORITIS  — bobot inverse-vol persis (butuh lot pecahan, tidak mungkin di broker)
  (b) PRAKTIS   — 0.01 lot di TIAP sleeve, yaitu satu-satunya yang bisa benar-benar
                  dijalankan dengan lot minimum. Inilah yang akan terjadi di demo.

Perbedaan keduanya penting: bobot inverse-vol memberi ORB 49% dan eterna 9%, tapi pada
lot minimum 0.01 semua sleeve mendapat ukuran yang sama. Angka (b) adalah kenyataan.

Jalankan: python research/portfolio_2026.py
"""
import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from portfolio_final import sleeve_eterna, sleeve_rsi2      # milik kami
from portfolio_audit import zrev_dollars, nas_dollars       # KODE TERVALIDASI USER

CAPITAL = 1000.0
SPLIT = pd.Timestamp("2026-01-01", tz="UTC")
END = pd.Timestamp("2026-08-01", tz="UTC")


def metrics(m, label):
    m = m.dropna()
    if len(m) == 0:
        return None
    eq = CAPITAL + m.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min()
    st_ = mx = 0
    for v in m:
        st_ = st_ + 1 if v < 0 else 0
        mx = max(mx, st_)
    return {"seri": label, "bln": len(m), "net$": round(m.sum()),
            "return%": round(100 * m.sum() / CAPITAL, 1),
            "maxDD%": round(100 * dd, 1),
            "hijau": f"{int((m>0).sum())}/{len(m)}",
            "beruntun": mx, "terburuk$": round(m.min())}


def main():
    print("Membangun 4 sleeve ...", flush=True)
    sl = {"ZREV_xau": zrev_dollars(), "ORB_nas": nas_dollars(),
          "ETERNA_xau": sleeve_eterna(), "RSI2_nas": sleeve_rsi2()}
    for k in sl:
        if sl[k].index.tz is None:
            sl[k].index = sl[k].index.tz_localize("UTC")
        print(f"  {k:<12} {len(sl[k]):>5} trade", flush=True)

    mon = pd.DataFrame({k: v.resample("ME").sum() for k, v in sl.items()}).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]

    train = mon[mon.index < SPLIT]
    test = mon[(mon.index >= SPLIT) & (mon.index < END)]
    print(f"\n  latih : {train.index[0]:%Y-%m} .. {train.index[-1]:%Y-%m}  ({len(train)} bulan)")
    print(f"  uji   : {test.index[0]:%Y-%m} .. {test.index[-1]:%Y-%m}  ({len(test)} bulan)")

    vol = train.std()
    w = (1 / vol) / (1 / vol).sum()
    print("\n  bobot inverse-vol DARI DATA LATIH SAJA (2021-2025):")
    for k, v in w.items():
        print(f"    {k:<12} {v:6.1%}")

    print("\n" + "=" * 100)
    print("A. PER SLEEVE — Jan..Jul 2026 (lot 0.01 masing-masing)")
    print("=" * 100)
    rows = [metrics(test[c], c) for c in test.columns]
    print(pd.DataFrame([r for r in rows if r]).to_string(index=False))

    print("\n" + "=" * 100)
    print("B. PORTOFOLIO — Jan..Jul 2026")
    print("=" * 100)
    teo = test @ w                       # bobot inverse-vol (butuh lot pecahan)
    prak = test.sum(axis=1)              # 0.01 lot tiap sleeve = yang benar-benar bisa dijalankan
    rows = [metrics(teo, "TEORITIS inverse-vol (lot pecahan)"),
            metrics(prak, "PRAKTIS 0.01 lot tiap sleeve")]
    print(pd.DataFrame([r for r in rows if r]).to_string(index=False))

    print("\n" + "=" * 100)
    print("C. RINCIAN BULANAN 2026 ($, modal $1000)")
    print("=" * 100)
    d = test.copy()
    d["TEORITIS"] = teo
    d["PRAKTIS"] = prak
    d.index = [f"{i:%b %Y}" for i in d.index]
    print(d.round(2).to_string())

    print("\n" + "=" * 100)
    print("D. PEMBANDING — periode latih 2021-2025 (in-sample)")
    print("=" * 100)
    rows = [metrics(train @ w, "TEORITIS inverse-vol"),
            metrics(train.sum(axis=1), "PRAKTIS 0.01 lot tiap sleeve")]
    print(pd.DataFrame([r for r in rows if r]).to_string(index=False))
    print("\n  Kalau angka 2026 jauh lebih buruk dari 2021-2025, itu tanda bobot/edge")
    print("  tidak bertahan keluar sampel. Kalau sebanding, portofolio ini nyata.")

    d.to_csv(r"C:\Quant\_MONITOR\portfolio_2026.csv")
    print("\nDisimpan: C:\\Quant\\_MONITOR\\portfolio_2026.csv")


if __name__ == "__main__":
    main()
