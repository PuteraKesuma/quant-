"""Stress-test what we DEPLOYED (not hunt new edges). Two quant questions:

  1. PARAMETER ROBUSTNESS — is the live zrev (N=20, EMA100, Daily50) on a broad
     plateau of good params (robust) or a lucky narrow spike (overfit)? Scan the
     neighbourhood; a real edge degrades GRACEFULLY as params move.
  2. EXIT mechanism — the trend-follower's main lever. Compare the current Donchian-
     channel exit vs an ATR CHANDELIER trail, at the deployed config.

OOS = 2025-01-01+. PnL $ @0.01 lot (XAU $1/pt). Run: python research/robustness_exits.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, per_year, mc_pf_p5
from cross_asset_edges import dual_sr   # generalized dual-filter S&R (channel exit)

XAU = load_m1("XAUUSD")
H = (XAU.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
       .dropna(subset=["open"]))


def oos_pf_dd(s):
    items = list(zip(s.index, s.values)); _, o = split(items)
    eq = s.sort_index().cumsum(); mdd = float((eq - eq.cummax()).min())
    so = stats(o)
    return so["pf"], so["net"], mdd, mc_pf_p5(o), len(s)


def atr(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def chandelier_sr(m1, cost=0.30, N=20, ema_n=100, dsma=50, k=3.0):
    """Always-in S&R but EXIT via ATR chandelier (price reverses k*ATR from the best
    price since entry) instead of the opposite Donchian channel. Entry = N-channel
    break gated by H1 EMA + Daily SMA (same as live)."""
    H1 = (m1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna(subset=["open"]))
    c = H1["close"].values; Hi = H1["high"].values; Lo = H1["low"].values; O = H1["open"].values
    up = pd.Series(H1["high"]).rolling(N).max().shift(1).values
    lo = pd.Series(H1["low"]).rolling(N).min().shift(1).values
    ema = H1["close"].ewm(span=ema_n, adjust=False).mean()
    h1_up = (H1["close"] > ema).shift(1).values
    av = atr(H1, 14).shift(1).values
    d1 = m1["close"].resample("1D").last(); pc = d1.shift(1); sma = d1.rolling(dsma).mean().shift(1)
    dmap = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts]))
                        else (1 if pc.loc[ts] > sma.loc[ts] else -1)) for ts in d1.index}
    dates = H1.index.date; idx = H1.index
    trades = []; pos = 0; ep = ets = None; best = None
    for i in range(len(H1)):
        if np.isnan(up[i]) or np.isnan(lo[i]) or np.isnan(av[i]):
            continue
        dt = dmap.get(dates[i], 0)
        can_long = bool(h1_up[i]) and dt == 1
        can_short = (not bool(h1_up[i])) and dt == -1
        if pos == 0:
            if Hi[i] >= up[i] and can_long: pos, ep, ets, best = 1, max(O[i], up[i]), idx[i], Hi[i]
            elif Lo[i] <= lo[i] and can_short: pos, ep, ets, best = -1, min(O[i], lo[i]), idx[i], Lo[i]
            continue
        if pos == 1:
            best = max(best, Hi[i])
            if Lo[i] <= best - k * av[i]:                       # chandelier exit
                trades.append((idx[i], (best - k * av[i] - ep) - cost)); pos = 0
        else:
            best = min(best, Lo[i])
            if Hi[i] >= best + k * av[i]:
                trades.append((idx[i], (ep - (best + k * av[i])) - cost)); pos = 0
    return pd.Series([p for _, p in trades], index=pd.DatetimeIndex([t for t, _ in trades]))


def main():
    print("=== 1. ROBUSTNESS SURFACE: OOS PF (dual-filter), deployed = N20/EMA100/D50 ===")
    print("    rows=entry_n N, cols=EMA period (daily SMA50 ON)")
    emas = [50, 100, 150, 200]
    print("    N \\ EMA   " + "  ".join(f"{e:>5}" for e in emas))
    for N in (10, 15, 20, 25, 30, 40):
        cells = []
        for e in emas:
            pf, net, dd, mc, n = oos_pf_dd(dual_sr(XAU, 0.30, N=N, ema_n=e, dsma=50))
            star = "*" if (N == 20 and e == 100) else " "
            cells.append(f"{pf:4.2f}{star}")
        print(f"    N={N:<3}     " + "  ".join(cells))
    print("    (* = config live. Plateau nilai mirip di sekitarnya = ROBUST; lonjakan terisolasi = overfit)")

    print("\n=== daily SMA sensitivity (N=20, EMA100) ===")
    for d in (0, 20, 50, 100):
        pf, net, dd, mc, n = oos_pf_dd(dual_sr(XAU, 0.30, N=20, ema_n=100, dsma=d) if d else
                                       dual_sr(XAU, 0.30, N=20, ema_n=100, dsma=1))
        lab = "OFF (H1 only)" if d in (0, 1) else f"SMA{d}"
        print(f"    daily {lab:14s}: OOS PF={pf:.2f} net=${net:+.0f} maxDD=${dd:.0f} MCp5={mc:.2f} n={n}")

    print("\n=== 2. EXIT: Donchian channel (live) vs ATR chandelier, N20/EMA100/D50 ===")
    pf, net, dd, mc, n = oos_pf_dd(dual_sr(XAU, 0.30, N=20, ema_n=100, dsma=50))
    print(f"    channel exit (LIVE): OOS PF={pf:.2f} net=${net:+.0f} maxDD=${dd:.0f} MCp5={mc:.2f} n={n}")
    for k in (2.0, 3.0, 4.0):
        pf, net, dd, mc, n = oos_pf_dd(chandelier_sr(XAU, k=k))
        print(f"    chandelier {k}xATR : OOS PF={pf:.2f} net=${net:+.0f} maxDD=${dd:.0f} MCp5={mc:.2f} n={n}")


if __name__ == "__main__":
    main()
