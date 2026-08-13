"""Menutup lubang: bandingkan SMC vs pembanding bodoh pada RISIKO YANG SETARA.

"bodoh menghasilkan 2.6x lebih banyak" tidak berarti apa-apa kalau drawdown-nya
juga 3x. Skrip ini menghitung maxDD tiap pembanding lalu menskalakan semuanya ke
drawdown yang sama (proxy penyetaraan risiko) supaya angkanya bisa diadu jujur.
"""
from __future__ import annotations
import warnings; warnings.filterwarnings("ignore")
import sys; from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(r"C:\Quant"); sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"research"))
from smc_xau_backtest import load_m1, tf, jalankan, CAPITAL
from smc_vs_bodoh import bodoh, BASE

m1 = load_m1(); h4 = tf(m1, "4h")

def ukur(t, nama):
    d = t.set_index("masuk").pnl.sort_index()
    eq = CAPITAL + d.cumsum()
    dd = abs(float(((eq - eq.cummax())/eq.cummax()).min()))
    w, l = d[d>0].sum(), -d[d<0].sum()
    mo = d.resample("ME").sum()
    sh = mo.mean()/mo.std(ddof=1)*np.sqrt(12) if mo.std(ddof=1)>0 else 0
    return {"strategi": nama, "n": len(d), "net$": round(d.sum(),2),
            "PF": round(w/l,2), "maxDD%": round(100*dd,1),
            "Sharpe": round(sh,2),
            "net per poin DD": round(d.sum()/(100*dd),1),
            "net @DD 15%": round(d.sum()*(0.148/dd),0)}

rows = [ukur(jalankan(h4, **BASE), "H4-B (OB+BOS+FVG)"),
        ukur(bodoh(h4, mode="market"), "bodoh BOS market"),
        ukur(bodoh(h4, mode="limit"), "bodoh BOS limit 30%"),
        ukur(bodoh(h4, mode="market", hanya_long=True), "bodoh BOS market LONG")]
print("\nPERBANDINGAN PADA RISIKO SETARA (kolom terakhir = diskalakan ke maxDD 14.8%)")
print("="*104)
print(pd.DataFrame(rows).to_string(index=False))
print("="*104)
