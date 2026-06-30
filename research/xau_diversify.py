"""Diversify WITHIN gold: run the validated zrev edge (dual-filter always-in S&R) on
different TIMEFRAMES (1H, 4H) and channel lengths, then combine. Each component is a
known profitable config (robustness surface); the question is whether combining lowers
the book's drawdown / raises risk-adjusted return (correlation < 1 => yes).

$ @0.01 lot ($1/point XAU). Correlation on monthly $-sums. OOS=2025+.
Run: python research/xau_diversify.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5

XAU = load_m1("XAUUSD")
D1 = XAU["close"].resample("1D").last().dropna()
DPC = D1.shift(1); DSMA = D1.rolling(50).mean().shift(1)
DMAP = {ts.date(): (0 if (np.isnan(DPC.loc[ts]) or np.isnan(DSMA.loc[ts]))
                    else (1 if DPC.loc[ts] > DSMA.loc[ts] else -1)) for ts in D1.index}


def zrev_tf(tf, N=20, ema_n=100, cost=0.30):
    B = (XAU.resample(tf).agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna(subset=["open"]))
    c = B["close"].values; H = B["high"].values; L = B["low"].values; O = B["open"].values
    up = pd.Series(B["high"]).rolling(N).max().shift(1).values
    lo = pd.Series(B["low"]).rolling(N).min().shift(1).values
    ema = B["close"].ewm(span=ema_n, adjust=False).mean()
    h1_up = (B["close"] > ema).shift(1).values
    dates = B.index.date; idx = B.index
    tr = []; pos = 0; ep = ets = None
    for i in range(len(B)):
        if np.isnan(up[i]) or np.isnan(lo[i]):
            continue
        dt = DMAP.get(dates[i], 0)
        cl = bool(h1_up[i]) and dt == 1; cs = (not bool(h1_up[i])) and dt == -1
        if pos == 0:
            if H[i] >= up[i] and cl: pos, ep, ets = 1, max(O[i], up[i]), idx[i]
            elif L[i] <= lo[i] and cs: pos, ep, ets = -1, min(O[i], lo[i]), idx[i]
            continue
        if pos == 1 and L[i] <= lo[i]:
            f = min(O[i], lo[i]); tr.append((idx[i], (f - ep) - cost)); pos, ep, ets = (-1, f, idx[i]) if cs else (0, None, None)
        elif pos == -1 and H[i] >= up[i]:
            f = max(O[i], up[i]); tr.append((idx[i], (ep - f) - cost)); pos, ep, ets = (1, f, idx[i]) if cl else (0, None, None)
    return pd.Series([p for _, p in tr], index=pd.DatetimeIndex([t for t, _ in tr]))


def m(name, s):
    items = list(zip(s.index, s.values)); _, o = split(items)
    eq = s.sort_index().cumsum(); dd = float((eq - eq.cummax()).min())
    so = stats(o); pf = "inf" if so["pf"] == float("inf") else f"{so['pf']:.2f}"
    span = max((s.index.max() - s.index.min()).days, 1)
    print(f"  {name:20s} n={len(s):4d} OOSpf={pf:>4} net=${s.sum():+6.0f} maxDD=${dd:+7.0f} "
          f"MC={mc_pf_p5(o):.2f} {len(s)/(span/7):.1f}/wk")
    return s


def book(name, series_list):
    both = pd.concat(series_list).sort_index()
    eq = both.cumsum(); dd = float((eq - eq.cummax()).min())
    items = list(zip(both.index, both.values)); _, o = split(items)
    so = stats(o); pf = "inf" if so["pf"] == float("inf") else f"{so['pf']:.2f}"
    print(f"  {name:20s} n={len(both):4d} OOSpf={pf:>4} net=${both.sum():+6.0f} maxDD=${dd:+7.0f} MC={mc_pf_p5(o):.2f}")


def main():
    print("Komponen zrev (dual-filter) per timeframe:\n")
    a = m("1H N20 (LIVE)", zrev_tf("1h", 20, 100))
    b = m("1H N30", zrev_tf("1h", 30, 100))
    c = m("4H N20", zrev_tf("4h", 20, 50))
    d = m("4H N10", zrev_tf("4h", 10, 50))
    print("\nKorelasi return bulanan:")
    M = {"1H20": a, "1H30": b, "4H20": c, "4H10": d}
    mm = {k: v.resample("MS").sum() for k, v in M.items()}
    df = pd.concat(mm, axis=1).fillna(0)
    print(df.corr().round(2).to_string())
    print("\nGABUNGAN (book) vs single 1H N20 (LIVE):")
    m("single 1H N20", a)
    book("1H20 + 4H20", [a, c])
    book("1H ens N15/20/30", [zrev_tf("1h", 15, 100), a, b])
    book("1H20 + 4H10", [a, d])
    print("\n  Diversifikasi MENOLONG jika gabungan: maxDD lebih kecil per unit net &/atau MC naik.")


if __name__ == "__main__":
    main()
