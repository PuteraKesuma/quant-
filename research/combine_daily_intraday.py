"""TASK 1 — quantify combining the DAILY book (trend/carry/reversal) with the INTRADAY book
(Z gold + NAS ORB). They're near-uncorrelated (different horizon) => stacking lifts the combined
Sharpe (the sqrt-breadth / low-corr lever). Reconstructs intraday daily PnL from the committed M1
engines, normalizes every stream to equal risk, and reports the correlation matrix + Sharpe of
daily-alone vs daily+intraday.

Run: python research/combine_daily_intraday.py
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant\research")
from tsmom_universe import UNIVERSE
from walkforward_trend import sharpe, FOLDS
from audit_live_strategies import load_m1, zrev_audit, nas_orb

COST = {n: c for n, _, _, c in UNIVERSE}
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")
SWAP = {"XAUUSD": (-6.30, 2.24), "NAS100": (-1.44, -0.31)}
RD = {y: v for y, v in zip(range(2011, 2027), [0,0,0,0,0.1,0.4,1.0,2.0,2.1,0.2,0.2,1.7,5.0,4.9,3.6,3.23])}


def unit(s):                                    # normalize any stream to unit daily std
    s = s.dropna(); return s / s.std() if s.std() > 0 else s


def daily_pnl(items):
    s = pd.Series([p for _, p in items], index=pd.DatetimeIndex([t for t, _ in items], tz="UTC"))
    return s.groupby(s.index.normalize()).sum()


# ---- daily book sleeves ----
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

trend = (dsl("XAUUSD") + dsl("NAS100")) / 2
carry = jsl()
revb = pd.concat({n: rev(n) for n in ("NAS100", "SP500", "NIKKEI")}, axis=1).mean(axis=1)

# ---- intraday edges from M1 ----
print("reconstructing intraday edges from M1 (may take ~30s)...")
z_int = daily_pnl(zrev_audit(load_m1("XAUUSD"))[0])
o_int = daily_pnl(nas_orb(load_m1("NAS100"))[0])
print(f"intraday Z gold: {len(z_int)} days  {z_int.index.min():%Y-%m} .. {z_int.index.max():%Y-%m}")
print(f"intraday NAS ORB: {len(o_int)} days\n")

# align on the DAILY grid over the intraday-available period; intraday non-trade days = 0 (flat)
lo = max(z_int.index.min(), o_int.index.min(), trend.dropna().index.min())
hi = min(z_int.index.max(), o_int.index.max(), trend.dropna().index.max())
grid = trend.dropna().index; grid = grid[(grid >= lo) & (grid <= hi)]
U = pd.DataFrame({
    "D:trend": unit(trend.reindex(grid)),
    "D:carry": unit(carry.reindex(grid)),
    "D:reversal": unit(revb.reindex(grid)),
    "I:Zgold": unit(z_int.reindex(grid).fillna(0)),
    "I:NASorb": unit(o_int.reindex(grid).fillna(0)),
}).dropna()
print(f"aligned daily grid: {U.index.min():%Y-%m-%d} .. {U.index.max():%Y-%m-%d}  ({len(U)} days)\n")
print("CORRELATION MATRIX (D=daily book, I=intraday book):")
print(U.corr().round(2).to_string())

def book_sharpe(cols):
    P = U[cols].mean(axis=1)
    return sharpe(P * np.sqrt(252)) if False else P.mean() / P.std() * np.sqrt(252)

daily_cols = ["D:trend", "D:carry", "D:reversal"]
all_cols = list(U.columns)
sd = book_sharpe(daily_cols); sa = book_sharpe(all_cols)
avg_intra_corr = U[daily_cols].corrwith(U[["I:Zgold","I:NASorb"]].mean(axis=1)).mean()
print(f"\nDAILY book alone  Sharpe = {sd:+.2f}")
print(f"DAILY + INTRADAY  Sharpe = {sa:+.2f}   (avg daily<->intraday corr {avg_intra_corr:+.2f})")
print(f"  -> uplift {sa-sd:+.2f} from stacking the near-uncorrelated intraday book")
print("DONE")
