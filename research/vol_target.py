"""Volatility targeting for the deployed zrev: size each trade so $ RISK is ~constant
(lot inversely proportional to the trade's risk = Donchian channel width at entry).
High-vol/wide-channel trade -> smaller lot; calm/tight -> bigger. Compare risk-adjusted
equity vs fixed lot and vs the deployed z-score (momentum) sizing, and the combo.

All schemes normalised to the SAME average lot (~2x = half-Kelly) so the risk budget is
equal -> a fair test of WHICH sizing shapes the equity best. OOS-aware. $ @0.01 base.
Run: python research/vol_target.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1

XAU = load_m1("XAUUSD")
H = (XAU.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
       .dropna(subset=["open"]))


def trades_with_risk(N=20, ema_n=100, dsma=50, cost=0.30):
    """Dual-filter always-in S&R; return per-trade (exit_ts, pnl, risk=channel width,
    zscore at entry)."""
    c = H["close"].values; Hi = H["high"].values; Lo = H["low"].values; O = H["open"].values
    up = pd.Series(H["high"]).rolling(N).max().shift(1).values
    lo = pd.Series(H["low"]).rolling(N).min().shift(1).values
    ema = H["close"].ewm(span=ema_n, adjust=False).mean()
    h1_up = (H["close"] > ema).shift(1).values
    ma20 = H["close"].rolling(20).mean().shift(1).values
    sd20 = H["close"].rolling(20).std().shift(1).values
    d1 = XAU["close"].resample("1D").last().dropna(); pc = d1.shift(1); sma = d1.rolling(dsma).mean().shift(1)
    dmap = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts]))
                        else (1 if pc.loc[ts] > sma.loc[ts] else -1)) for ts in d1.index}
    dates = H.index.date; idx = H.index
    out = []; pos = 0; ep = ets = None; risk0 = z0 = 0.0
    for i in range(len(H)):
        if np.isnan(up[i]) or np.isnan(lo[i]) or np.isnan(sd20[i]) or sd20[i] <= 0:
            continue
        dt = dmap.get(dates[i], 0)
        can_long = bool(h1_up[i]) and dt == 1
        can_short = (not bool(h1_up[i])) and dt == -1
        if pos == 0:
            if Hi[i] >= up[i] and can_long:
                pos, ep, ets = 1, max(O[i], up[i]), idx[i]; risk0 = up[i] - lo[i]; z0 = (c[i] - ma20[i]) / sd20[i]
            elif Lo[i] <= lo[i] and can_short:
                pos, ep, ets = -1, min(O[i], lo[i]), idx[i]; risk0 = up[i] - lo[i]; z0 = -(c[i] - ma20[i]) / sd20[i]
            continue
        if pos == 1 and Lo[i] <= lo[i]:
            f = min(O[i], lo[i]); out.append((idx[i], (f - ep) - cost, risk0, z0))
            if can_short: pos, ep, ets = -1, f, idx[i]; risk0 = up[i] - lo[i]; z0 = -(c[i] - ma20[i]) / sd20[i]
            else: pos = 0
        elif pos == -1 and Hi[i] >= up[i]:
            f = max(O[i], up[i]); out.append((idx[i], (ep - f) - cost, risk0, z0))
            if can_long: pos, ep, ets = 1, f, idx[i]; risk0 = up[i] - lo[i]; z0 = (c[i] - ma20[i]) / sd20[i]
            else: pos = 0
    return out


def mdd(e):
    peak = np.maximum.accumulate(e); return float((e / peak - 1).min())


def evaluate(name, pnl, lot, ts):
    lot = lot * (2.0 / lot.mean())                 # same avg risk (~half-Kelly) for all schemes
    order = np.argsort(ts)
    eq = 1500 + np.cumsum(pnl[order] * lot[order])
    net = eq[-1] - 1500; dd = mdd(np.r_[1500, eq])
    calmar = net / abs(dd * 1500) if dd < 0 else 9
    print(f"  {name:26s} net=${net:+6.0f}  maxDD={100*dd:5.0f}%  return/DD={net/abs(dd*1500):.2f}")


def main():
    tr = trades_with_risk()
    ts = np.array([t[0].value for t in tr], dtype="int64")
    pnl = np.array([t[1] for t in tr]); risk = np.array([t[2] for t in tr]); z = np.array([t[3] for t in tr])
    print(f"trades={len(tr)}  (start $1500, all schemes same avg lot=0.02)\n")
    one = np.ones(len(tr))
    evaluate("fixed", pnl, one, ts)
    evaluate("vol-target (1/risk)", pnl, 1.0 / risk, ts)
    evaluate("z-score (deployed)", pnl, pd.Series(z).rank(pct=True).values + 0.01, ts)
    evaluate("z-score x vol-target", pnl, (pd.Series(z).rank(pct=True).values + 0.01) / risk, ts)
    print("\n  (return/DD lebih tinggi = sizing membentuk equity lebih efisien)")


if __name__ == "__main__":
    main()
