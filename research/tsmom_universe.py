"""Partner research #1b — diversified cross-asset Time-Series Momentum (the REAL TSMOM test).
Fetches DAILY bars for ~21 instruments across 5 asset classes straight from Dukascopy (cached),
then runs vol-targeted SMA-sign trend-following: per-asset + per-asset-CLASS portfolio + all-in
portfolio, cost-aware, IS/OOS split, per-year. This is where the published TSMOM edge actually lives
(diversification across many trending markets), unlike FX-majors-only.

Run: python research/tsmom_universe.py
"""
import os, datetime as dt
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd, dukascopy_python

CACHE = r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet"
START = dt.datetime(2011, 1, 1); END = dt.datetime(2026, 6, 26)
OOS_FROM = pd.Timestamp("2023-01-01", tz="UTC")
LKB = [50, 100, 200]; TARGET_VOL = 0.10

# (name, dukascopy raw instrument string, asset_class, per-flip cost in return terms)
UNIVERSE = [
    ("EURUSD", "EUR/USD", "fx", 0.00005),
    ("GBPUSD", "GBP/USD", "fx", 0.00008),
    ("USDJPY", "USD/JPY", "fx", 0.00006),
    ("USDCHF", "USD/CHF", "fx", 0.00008),
    ("AUDUSD", "AUD/USD", "fx", 0.00008),
    ("USDCAD", "USD/CAD", "fx", 0.00009),
    ("NZDUSD", "NZD/USD", "fx", 0.00012),
    ("XAUUSD", "XAU/USD", "metal", 0.0002),
    ("XAGUSD", "XAG/USD", "metal", 0.0004),
    ("WTI",    "E_Light", "energy", 0.0004),
    ("BRENT",  "E_Brent", "energy", 0.0004),
    ("NATGAS", "GAS.CMD/USD", "energy", 0.0006),
    ("COPPER", "COPPER.CMD/USD", "metal", 0.0004),
    ("NAS100", "E_NQ-100", "index", 0.0002),
    ("SP500",  "E_SandP-500", "index", 0.0002),
    ("DOW",    "E_D&J-Ind", "index", 0.0002),
    ("DAX",    "E_DAAX", "index", 0.0002),
    ("FTSE",   "E_Futsee-100", "index", 0.0002),
    ("NIKKEI", "E_N225Jap", "index", 0.0003),
    ("HANGSENG", "E_H-Kong", "index", 0.0003),
    ("DXY",    "DOLLAR.IDX/USD", "macro", 0.0001),
]


def fetch_universe():
    if os.path.exists(CACHE):
        px = pd.read_parquet(CACHE)
        if all(n in px.columns for n,_,_,_ in UNIVERSE):
            print(f"loaded cache {CACHE}  {px.shape}"); return px
    cols = {}
    for name, code, cls, _ in UNIVERSE:
        try:
            df = dukascopy_python.fetch(code, dukascopy_python.INTERVAL_DAY_1, dukascopy_python.OFFER_SIDE_BID, START, END)
            df = df.rename(columns=str.lower); s = df["close"].copy(); s.index = pd.to_datetime(s.index, utc=True)
            cols[name] = s.resample("1D").last()
            print(f"  {name:9} {len(s):>5} daily bars  {s.index.min():%Y-%m} -> {s.index.max():%Y-%m}")
        except Exception as e:
            print(f"  {name:9} FETCH FAILED: {e}")
    px = pd.DataFrame(cols).dropna(how="all")
    os.makedirs(os.path.dirname(CACHE), exist_ok=True); px.to_parquet(CACHE)
    print(f"cached -> {CACHE}  {px.shape}"); return px


def stats(r):
    r = r.dropna()
    if len(r) < 100: return None
    sh = r.mean()/r.std()*np.sqrt(252) if r.std() > 0 else 0
    eq = (1+r).cumprod(); dd = (eq/eq.cummax()-1).min(); cagr = eq.iloc[-1]**(252/len(r))-1
    return dict(sh=sh, cagr=cagr, dd=dd, n=len(r))


def tsmom(close, cost, L):
    ret = close.pct_change()
    sma = close.rolling(L).mean(); pos = np.sign(close - sma).shift(1)
    vol = ret.rolling(50).std().shift(1)*np.sqrt(252)
    scale = (TARGET_VOL/vol).clip(upper=3).fillna(0)
    net = pos*scale*ret - (pos.diff().abs().fillna(0))*scale*cost
    return net


def main():
    px = fetch_universe()
    meta = {n:(cls,cost) for n,_,cls,cost in UNIVERSE}
    print(f"\nDIVERSIFIED TSMOM  vol-target {TARGET_VOL:.0%}  OOS from {OOS_FROM.date()}")
    print(f"{'asset':9} {'cls':6} {'L':>4} {'Sharpe':>7} {'OOS':>6} {'CAGR':>7} {'maxDD':>7} {'grn'}")
    series = {L: {} for L in LKB}; best_by_asset = {}
    for name in px.columns:
        if name not in meta: continue
        cls, cost = meta[name]; c = px[name].dropna()
        if len(c) < 400: continue
        best = None
        for L in LKB:
            net = tsmom(c, cost, L); f = stats(net); o = stats(net[net.index >= OOS_FROM])
            if f is None: continue
            yr = net.groupby(net.index.year).sum(); grn = f"{int((yr>0).sum())}/{int(yr.notna().sum())}"
            series[L][name] = (net, cls)
            osh = o['sh'] if o else float('nan')
            print(f"{name:9} {cls:6} {L:>4} {f['sh']:>7.2f} {osh:>6.2f} {f['cagr']:>7.1%} {f['dd']:>7.1%} {grn}")
            if o and (best is None or f['sh']+osh > best[1]): best = (L, f['sh']+osh, f['sh'], osh)
        best_by_asset[name] = best
        print()

    def port(netmap):
        if not netmap: return None
        P = pd.concat([v[0] for v in netmap.values()], axis=1).mean(axis=1)
        return stats(P), stats(P[P.index >= OOS_FROM])
    print("==== PORTFOLIOS (equal-risk avg of vol-scaled assets) ====")
    for L in LKB:
        f,o = port(series[L]); print(f"  ALL      L={L:>3}: Sharpe {f['sh']:.2f}  OOS {o['sh'] if o else float('nan'):.2f}  CAGR {f['cagr']:.1%}  maxDD {f['dd']:.1%}")
    print("  --- by asset class (L=100) ---")
    for cls in ["fx","metal","energy","index","macro"]:
        sub = {k:v for k,v in series[100].items() if v[1]==cls}
        r = port(sub)
        if r: print(f"    {cls:7}: Sharpe {r[0]['sh']:.2f}  OOS {r[1]['sh'] if r[1] else float('nan'):.2f}  ({len(sub)} assets)")
    print("\n==== per-asset trend edge (best L by full+OOS Sharpe) ====")
    rows=[(n,b[0],b[2],b[3]) for n,b in best_by_asset.items() if b]
    for n,L,sh,osh in sorted(rows,key=lambda x:-(x[2]+x[3])):
        flag = "  <-- STRONG" if (sh>0.5 and osh>0.4) else ("  <- ok" if sh>0.3 and osh>0.2 else "")
        print(f"  {n:9} L={L:>3}  full {sh:+.2f}  OOS {osh:+.2f}{flag}")
    print("DONE")


if __name__ == "__main__":
    main()
