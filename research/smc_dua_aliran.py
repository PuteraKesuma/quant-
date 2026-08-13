"""H1-C sebagai ALIRAN KEDUA di samping H4-B — apakah menambah nilai ke BUKU?

Standalone H1-C biasa saja (+$132, PF 1.10, maxDD -44%). Tapi pelajaran ZREV:
untung sendirian != berguna di buku, dan sebaliknya sleeve biasa-biasa saja bisa
berharga kalau korelasinya rendah. Yang menentukan Calmar portofolio, bukan PF-nya.
"""
from __future__ import annotations
import warnings, sys; warnings.filterwarnings("ignore")
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(r"C:\Quant"); sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"research"))
from smc_xau_backtest import load_m1, tf, jalankan, malam, SWAP_LONG, SWAP_SHORT, CAPITAL
from blocking_akurat import load_h1, eterna_trades
from portfolio_audit import nas_dollars

m1 = load_m1()
h4b = jalankan(tf(m1,"4h"), k=3, ob_lookback=10, expiry=12, rr=2.0, buffer_frac=0.10,
               pakai_fvg=True, pakai_sweep=False)
h1c = jalankan(tf(m1,"1h"), k=3, ob_lookback=10, expiry=12, rr=2.0, buffer_frac=0.10,
               pakai_fvg=False, pakai_sweep=True)
et = eterna_trades(load_h1())
et["malam"] = [malam(a,b) for a,b in zip(et.masuk, et.keluar)]
et["p"] = et.pnl + np.where(et.arah==1, et.malam*SWAP_LONG, et.malam*SWAP_SHORT)
orb = nas_dollars()
if orb.index.tz is None: orb.index = orb.index.tz_localize("UTC")

mo = pd.DataFrame({"ORB": orb.resample("ME").sum(),
                   "ETERNA": et.set_index("masuk").p.resample("ME").sum(),
                   "H4B": h4b.set_index("masuk").pnl.resample("ME").sum(),
                   "H1C": h1c.set_index("masuk").pnl.resample("ME").sum()}).fillna(0.0)
mo = mo.loc[(mo!=0).any(axis=1)]
print("korelasi imbal bulanan:"); print(mo.corr().round(2).to_string())

def ukur(cols):
    p = mo[cols].sum(axis=1); eq = CAPITAL + p.cumsum()
    dd = float(((eq-eq.cummax())/eq.cummax()).min()); yrs = len(p)/12
    cagr = (eq.iloc[-1]/CAPITAL)**(1/yrs)-1; r = p/CAPITAL
    return {"buku": "+".join(cols), "CAGR%": round(100*cagr,1), "maxDD%": round(100*dd,1),
            "Calmar": round(cagr/abs(dd),2), "Sharpe": round(r.mean()/r.std(ddof=1)*np.sqrt(12),2),
            "hijau%": round(100*(p>0).mean())}
print("\n"+"="*88)
rows=[ukur(["ORB","ETERNA"]), ukur(["ORB","ETERNA","H4B"]), ukur(["ORB","ETERNA","H4B","H1C"])]
print(pd.DataFrame(rows).to_string(index=False))
c2, c3 = rows[1]["Calmar"], rows[2]["Calmar"]
print("\nmenambah H1C sebagai aliran kedua: Calmar %.2f -> %.2f (%+.2f) -> %s" % (
    c2, c3, c3-c2, "MEMBAIK" if c3>c2 else "MEMBURUK"))
n_tot = len(h4b)+len(h1c); hari=(m1.index[-1]-m1.index[0]).days
print("jumlah order SMC: %d (H4B) + %d (H1C) = %d  -> %.2f/hari (dari %.2f/hari)" % (
    len(h4b), len(h1c), n_tot, n_tot/hari, len(h4b)/hari))
