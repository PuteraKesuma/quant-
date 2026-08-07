"""Equity curve of the COMBINED VALIDATED BOOK from $400, Jan 2026 -> now.
Book = gold-trend + NAS-trend + JPY-carry x0.5 + equity-reversal (NAS+SP+NIKKEI), real cost, equal-risk.
Shows leverage 1x/2x/3x (honest: return scales, DD scales). Writes JSON series for the chart.
"""
import os, json, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd, dukascopy_python
from tsmom_universe import UNIVERSE

COST = {n: c for n, _, _, c in UNIVERSE}
# fetch FRESH daily to now for the assets the book needs (cached parquet is stale)
CODES = {"XAUUSD": "XAU/USD", "XAGUSD": "XAG/USD", "NAS100": "E_NQ-100",
         "SP500": "E_SandP-500", "NIKKEI": "E_N225Jap", "USDJPY": "USD/JPY"}
def _fetch(code):
    df = dukascopy_python.fetch(code, dukascopy_python.INTERVAL_DAY_1, dukascopy_python.OFFER_SIDE_BID,
                                dt.datetime(2015, 1, 1), dt.datetime.utcnow())
    df = df.rename(columns=str.lower); s = df["close"].copy(); s.index = pd.to_datetime(s.index, utc=True)
    return s.resample("1D").last().dropna()
px = pd.DataFrame({n: _fetch(c) for n, c in CODES.items()}).dropna(how="all")
print(f"fresh data thru {px.index.max():%Y-%m-%d}\n")
SWAP = {"XAUUSD": (-6.30, 2.24), "NAS100": (-1.44, -0.31)}
RD = {y: v for y, v in zip(range(2011, 2027), [0,0,0,0,0.1,0.4,1.0,2.0,2.1,0.2,0.2,1.7,5.0,4.9,3.6,3.23])}
START_CASH, Y0 = 400.0, pd.Timestamp("2026-01-01", tz="UTC")


def _sc(x): return (0.10 / (x.rolling(50).std().shift(1) * np.sqrt(252))).clip(upper=3).fillna(0)
def dsl(nm):
    c = px[nm].dropna(); rt = c.pct_change(); u = c.rolling(100).max().shift(1); dn = c.rolling(100).min().shift(1)
    p = pd.Series(np.nan, index=c.index); p[c >= u] = 1; p[c <= dn] = -1; p = p.ffill().shift(1); sc = _sc(rt)
    sl, ss = SWAP[nm]; sw = pd.Series(np.where(p > 0, sl, np.where(p < 0, ss, 0.0)), index=c.index) / 100 / 252
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST[nm] + p.abs() * sc * sw
def jsl():
    c = px["USDJPY"].dropna(); rt = c.pct_change(); net = pd.Series([(RD.get(y,3.23)-2.32)/100/252 for y in c.index.year], index=c.index)
    p = ((c > c.rolling(100).mean()) & (net > 0)).astype(float).shift(1).fillna(0); sc = _sc(rt)
    return p * sc * rt - p.diff().abs().fillna(0) * sc * COST["USDJPY"] + p * sc * net.clip(lower=0)
def rsi(c, n=2):
    d = c.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))
def rev(name):
    c = px[name].dropna(); ret = c.pct_change(); r2 = rsi(c, 2).values
    up = (c > c.rolling(200).mean()).values; ex = (c > c.rolling(5).mean()).values
    pos = np.zeros(len(c)); ip = False
    for i in range(1, len(c)):
        if not ip and r2[i] < 10 and up[i]: ip = True
        elif ip and ex[i]: ip = False
        pos[i] = 1.0 if ip else 0.0
    pos = pd.Series(pos, index=c.index).shift(1).fillna(0)
    r = pos * ret - pos.diff().abs().fillna(0) * COST[name]
    return (r / (r.rolling(50).std() * np.sqrt(252)) * 0.10).replace([np.inf, -np.inf], 0).fillna(0)


revb = pd.concat({n: rev(n) for n in ("NAS100", "SP500", "NIKKEI")}, axis=1).mean(axis=1)
B = pd.concat({"g": dsl("XAUUSD"), "n": dsl("NAS100"), "j": jsl(), "r": revb}, axis=1).dropna()
# IMPROVED allocation (Sharpe 0.70->0.81): equal-risk across the 3 sleeve TYPES, lean off mediocre trend
trend = (B["g"] + B["n"]) / 2
book = (trend + B["j"] + B["r"]) / 3
r26 = book[book.index >= Y0].dropna()

out = {"dates": [d.strftime("%Y-%m-%d") for d in r26.index], "series": {}}
print(f"COMBINED BOOK equity from ${START_CASH:.0f}  ({r26.index[0]:%Y-%m-%d} .. {r26.index[-1]:%Y-%m-%d}, {len(r26)} days)\n")
print(f"  {'lever':>5} {'final $':>9} {'return':>8} {'maxDD':>7}")
for L in (1, 2, 3):
    eq = START_CASH * (1 + r26 * L).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    out["series"][f"{L}x"] = [round(v, 2) for v in eq.values]
    print(f"  {L:>4}x {eq.iloc[-1]:>9.2f} {eq.iloc[-1]/START_CASH-1:>+7.1%} {dd:>7.1%}")
out["start"] = START_CASH
with open(r"C:\Quant\_MONITOR\equity_curve_400.json", "w") as f:
    json.dump(out, f)
print("\nwrote _MONITOR/equity_curve_400.json")
print("DONE")
