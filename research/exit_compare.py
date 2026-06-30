"""Find the best EXIT for the deployed zrev (the trend-follower's main lever).

Entry is held IDENTICAL to live (dual-filter Donchian N-break: H1 EMA100 + Daily SMA50
gate). Only the EXIT varies:
  - channel    : opposite N-channel break (current/live, = always-in S&R)
  - chandelier : trail k*ATR from the best price since entry
  - atrstop    : hard stop at entry -/+ k*ATR (no trail) + channel backstop
  - ema        : exit when H1 close crosses back through the EMA
  - time       : exit after M bars
After a non-channel exit -> flat, re-enter on the next valid breakout.

OOS=2025+. $ @0.01 lot. Diagnostics print entries so 0-trade bugs are visible.
Run: python research/exit_compare.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, per_year, mc_pf_p5

XAU = load_m1("XAUUSD")
H = (XAU.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
       .dropna(subset=["open"]))


def atr(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def backtest(exit_type, k=3.0, m=24, cost=0.30, N=20, ema_n=100, dsma=50):
    c = H["close"].values; Hi = H["high"].values; Lo = H["low"].values; O = H["open"].values
    up = pd.Series(H["high"]).rolling(N).max().shift(1).values
    lo = pd.Series(H["low"]).rolling(N).min().shift(1).values
    ema = H["close"].ewm(span=ema_n, adjust=False).mean()
    ema_v = ema.values
    h1_up = (H["close"] > ema).shift(1).values
    av = atr(H, 14).shift(1).values
    d1 = XAU["close"].resample("1D").last().dropna(); pc = d1.shift(1); sma = d1.rolling(dsma).mean().shift(1)
    dmap = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts]))
                        else (1 if pc.loc[ts] > sma.loc[ts] else -1)) for ts in d1.index}
    dates = H.index.date; idx = H.index
    trades = []; pos = 0; ep = None; ei = 0; best = None; n_ent = 0
    for i in range(len(H)):
        if np.isnan(up[i]) or np.isnan(lo[i]) or np.isnan(av[i]):
            continue
        dt = dmap.get(dates[i], 0)
        can_long = (h1_up[i] is True or h1_up[i] == 1) and dt == 1
        can_short = (h1_up[i] is False or h1_up[i] == 0) and dt == -1
        if pos == 0:
            if Hi[i] >= up[i] and can_long: pos, ep, ei, best = 1, max(O[i], up[i]), i, Hi[i]; n_ent += 1
            elif Lo[i] <= lo[i] and can_short: pos, ep, ei, best = -1, min(O[i], lo[i]), i, Lo[i]; n_ent += 1
            continue
        # ----- in position: exit logic -----
        x = None
        if pos == 1:
            best = max(best, Hi[i])
            if exit_type == "channel" and Lo[i] <= lo[i]: x = min(O[i], lo[i])
            elif exit_type == "chandelier" and Lo[i] <= best - k * av[i]: x = best - k * av[i]
            elif exit_type == "atrstop" and (Lo[i] <= ep - k * av[ei] or Lo[i] <= lo[i]):
                x = max(ep - k * av[ei], min(O[i], lo[i]))
            elif exit_type == "ema" and c[i] < ema_v[i]: x = c[i]
            elif exit_type == "time" and i - ei >= m: x = c[i]
            if x is not None: trades.append((idx[i], (x - ep) - cost)); pos = 0
        else:
            best = min(best, Lo[i])
            if exit_type == "channel" and Hi[i] >= up[i]: x = max(O[i], up[i])
            elif exit_type == "chandelier" and Hi[i] >= best + k * av[i]: x = best + k * av[i]
            elif exit_type == "atrstop" and (Hi[i] >= ep + k * av[ei] or Hi[i] >= up[i]):
                x = min(ep + k * av[ei], max(O[i], up[i]))
            elif exit_type == "ema" and c[i] > ema_v[i]: x = c[i]
            elif exit_type == "time" and i - ei >= m: x = c[i]
            if x is not None: trades.append((idx[i], (ep - x) - cost)); pos = 0
    return pd.Series([p for _, p in trades], index=pd.DatetimeIndex([t for t, _ in trades])), n_ent


def rep(tag, s, n_ent):
    if len(s) == 0:
        print(f"  {tag:22s} entries={n_ent} trades=0"); return
    items = list(zip(s.index, s.values)); _, o = split(items)
    eq = s.sort_index().cumsum(); mdd = float((eq - eq.cummax()).min())
    py = per_year(items); g = sum(1 for v in py.values() if v[0] >= 1.0)
    so = stats(o); pf = "inf" if so["pf"] == float("inf") else f"{so['pf']:.2f}"
    print(f"  {tag:22s} n={len(s):4d} OOSpf={pf:>4} OOSnet=${so['net']:+6.0f} "
          f"maxDD=${mdd:+7.0f} MCp5={mc_pf_p5(o):.2f} grn{g}/{len(py)}")


def main():
    print("EXIT comparison (entry identical = dual-filter N20/EMA100/D50):\n")
    s, e = backtest("channel"); rep("channel (LIVE)", s, e)
    for k in (2.0, 3.0, 4.0, 5.0):
        s, e = backtest("chandelier", k=k); rep(f"chandelier {k}xATR", s, e)
    for k in (2.0, 3.0, 4.0):
        s, e = backtest("atrstop", k=k); rep(f"atrstop {k}xATR+chan", s, e)
    s, e = backtest("ema"); rep("ema cross", s, e)
    for m in (12, 24, 48):
        s, e = backtest("time", m=m); rep(f"time {m}bar", s, e)


if __name__ == "__main__":
    main()
