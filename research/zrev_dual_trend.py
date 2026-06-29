"""Validate a MULTI-TIMEFRAME trend filter for zrev: only trade when the H1 EMA100
trend AND the DAILY SMA50 trend agree (against-trend break -> flat, not reverse).

Motivated by the DD anatomy: the drawdown is driven by counter-daily-trend trades
(shorting gold's secular uptrend). 'Align with the higher timeframe' is a robust
principle (works in bull OR bear), so this is more than a gold-bull bet -- but we
verify IS/OOS + MC + walk-forward before recommending deploy.

No-lookahead: H1 EMA & daily trend use completed bars (shift 1 / prior day).
Run: python research/zrev_dual_trend.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1, stats, split, fmt, per_year, mc_pf_p5

XAU = load_m1("XAUUSD")
H1 = (XAU.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
         .dropna(subset=["open"]))


def daily_map(sma_n=50):
    d1 = to_d1(XAU); dc = d1["close"]; pc = dc.shift(1); sma = dc.rolling(sma_n).mean().shift(1)
    return {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts]))
                        else (1 if pc.loc[ts] > sma.loc[ts] else -1)) for ts in d1.index}


def sim_dual(N=20, ema_n=100, dmap=None, use_daily=True, cost=0.30):
    c = H1["close"].values; H = H1["high"].values; L = H1["low"].values; O = H1["open"].values
    up = pd.Series(H1["high"]).rolling(N).max().shift(1).values
    lo = pd.Series(H1["low"]).rolling(N).min().shift(1).values
    ema = H1["close"].ewm(span=ema_n, adjust=False).mean()
    h1_up = (H1["close"] > ema).shift(1).values          # H1 trend, completed-bar
    dates = H1.index.date; idx = H1.index
    trades = []; pos = 0; ep = ets = None
    for i in range(len(H1)):
        if np.isnan(up[i]) or np.isnan(lo[i]) or (isinstance(h1_up[i], float) and np.isnan(h1_up[i])):
            continue
        dt = dmap.get(dates[i], 0) if (use_daily and dmap) else None
        can_long = bool(h1_up[i]) and (dt == 1 if use_daily else True)
        can_short = (not bool(h1_up[i])) and (dt == -1 if use_daily else True)
        if pos == 0:
            if H[i] >= up[i] and can_long: pos, ep, ets = 1, max(O[i], up[i]), idx[i]
            elif L[i] <= lo[i] and can_short: pos, ep, ets = -1, min(O[i], lo[i]), idx[i]
            continue
        if pos == 1:
            if L[i] <= lo[i]:
                f = min(O[i], lo[i]); trades.append((ets, idx[i], "long", (f - ep) - cost))
                if L[i] <= lo[i] and can_short: pos, ep, ets = -1, f, idx[i]
                else: pos = 0
        else:
            if H[i] >= up[i]:
                f = max(O[i], up[i]); trades.append((ets, idx[i], "short", (ep - f) - cost))
                if H[i] >= up[i] and can_long: pos, ep, ets = 1, f, idx[i]
                else: pos = 0
    return trades


def audit(tag, trades):
    items = [(t[1], t[3]) for t in trades]      # (exit_ts, pnl)
    i_, o = split(items)
    pnl = np.array([p for _, p in items]); eq = np.cumsum(pnl); mdd = (eq - np.maximum.accumulate(eq)).min()
    wf = pd.Series(pnl, index=pd.DatetimeIndex([t for t, _ in items])).resample("6MS").sum()
    span = (items[-1][0] - items[0][0]).days
    print(f"{tag}")
    print(f"  ALL n={len(items)} net=${pnl.sum():+.0f} PF={stats([p for _,p in items])['pf']:.2f} "
          f"maxDD=${mdd:.0f}  {len(items)/(span/7):.1f}/wk")
    print(f"  IS  {fmt(stats(i_))}")
    print(f"  OOS {fmt(stats(o))}")
    py = per_year(items); g = sum(1 for v in py.values() if v[0] >= 1.0)
    pys = "  ".join(f"{y}:{min(v[0],9.99):.2f}" for y, v in py.items())
    print(f"  per-year PF: {pys}  green {g}/{len(py)}")
    print(f"  MC OOS p5: {mc_pf_p5(o):.2f}   walk-forward: {int((wf>0).sum())}/{len(wf)} hijau")


def main():
    dmap = daily_map(50)
    print("BASELINE (H1 EMA100 only — live sekarang):")
    audit("  ", sim_dual(dmap=dmap, use_daily=False))
    print("\nDUAL (H1 EMA100 + Daily SMA50 align):")
    audit("  ", sim_dual(dmap=dmap, use_daily=True))


if __name__ == "__main__":
    main()
