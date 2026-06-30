"""Quantify profit-lock mechanisms for the deployed zrev (dual-filter entry20/exit20
+ EMA100 + Daily50). The user's pain: a trade ran +$66 then gave back to +$26 because
the channel trailing exit is slow. Trade-off: locking profit reduces give-backs but
(earlier exit tests) tends to lower PF. Measure BOTH sides honestly.

Mechanisms (entry identical):
  - channel        : opposite N-channel break (LIVE)
  - be Xatr        : once favourable >= X*ATR(entry), stop -> entry (breakeven lock)
  - trail X/Yatr   : once favourable >= X*ATR, trail stop at best -/+ Y*ATR (lock gains)
Exit = first of {profit-lock stop, channel}. After a non-channel exit -> flat, re-enter.

Metrics: OOS PF/net/DD/MC AND give-back ($ peak-profit returned on winners) and
'round-trip to loss' count (reached >=+$25 then closed <=0). $ @0.01 lot.
Run: python research/profit_lock.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5

XAU = load_m1("XAUUSD")
H = (XAU.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
       .dropna(subset=["open"]))


def atr(h, n=14):
    tr = pd.concat([h["high"] - h["low"], (h["high"] - h["close"].shift()).abs(),
                    (h["low"] - h["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def sim(mode, arm=2.0, lock=2.0, cost=0.30, N=20, ema_n=100, dsma=50):
    c = H["close"].values; Hi = H["high"].values; Lo = H["low"].values; O = H["open"].values
    up = pd.Series(H["high"]).rolling(N).max().shift(1).values
    lo = pd.Series(H["low"]).rolling(N).min().shift(1).values
    ema = H["close"].ewm(span=ema_n, adjust=False).mean()
    h1_up = (H["close"] > ema).shift(1).values
    av = atr(H, 14).shift(1).values
    d1 = XAU["close"].resample("1D").last().dropna(); pc = d1.shift(1); sma = d1.rolling(dsma).mean().shift(1)
    dmap = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts]))
                        else (1 if pc.loc[ts] > sma.loc[ts] else -1)) for ts in d1.index}
    dates = H.index.date; idx = H.index
    tr = []   # (exit_ts, pnl, mfe)
    pos = 0; ep = ets = None; best = None; ea = 0.0
    for i in range(len(H)):
        if np.isnan(up[i]) or np.isnan(lo[i]) or np.isnan(av[i]):
            continue
        dt = dmap.get(dates[i], 0)
        can_long = bool(h1_up[i]) and dt == 1
        can_short = (not bool(h1_up[i])) and dt == -1
        if pos == 0:
            if Hi[i] >= up[i] and can_long: pos, ep, ets, best, ea = 1, max(O[i], up[i]), idx[i], Hi[i], av[i]
            elif Lo[i] <= lo[i] and can_short: pos, ep, ets, best, ea = -1, min(O[i], lo[i]), idx[i], Lo[i], av[i]
            continue
        x = None
        if pos == 1:
            best = max(best, Hi[i]); fav = best - ep
            plock = None
            if mode == "be" and fav >= arm * ea: plock = ep
            elif mode == "trail" and fav >= arm * ea: plock = best - lock * ea
            if plock is not None and Lo[i] <= plock: x = min(plock, O[i] if O[i] < plock else plock)
            elif Lo[i] <= lo[i]: x = min(O[i], lo[i])
            if x is not None: tr.append((idx[i], (x - ep) - cost, best - ep)); pos = 0
        else:
            best = min(best, Lo[i]); fav = ep - best
            plock = None
            if mode == "be" and fav >= arm * ea: plock = ep
            elif mode == "trail" and fav >= arm * ea: plock = best + lock * ea
            if plock is not None and Hi[i] >= plock: x = max(plock, O[i] if O[i] > plock else plock)
            elif Hi[i] >= up[i]: x = max(O[i], up[i])
            if x is not None: tr.append((idx[i], (ep - x) - cost, ep - best)); pos = 0
    return tr


def rep(tag, tr):
    items = [(t[0], t[1]) for t in tr]; _, o = split(items)
    pnl = np.array([t[1] for t in tr]); mfe = np.array([t[2] for t in tr])
    eq = np.cumsum(pnl); mdd = (eq - np.maximum.accumulate(eq)).min()
    so = stats(o); pf = "inf" if so["pf"] == float("inf") else f"{so['pf']:.2f}"
    win = pnl > 0
    giveback = float((mfe[win] - pnl[win]).mean()) if win.any() else 0          # avg $ peak given back on winners
    roundtrip = int(((mfe >= 25) & (pnl <= 0)).sum())                            # reached +$25 then closed <=0
    print(f"  {tag:20s} n={len(tr):4d} OOSpf={pf:>4} net=${pnl.sum():+6.0f} maxDD=${mdd:+7.0f} "
          f"MC={mc_pf_p5(o):.2f} | giveback=${giveback:4.1f} roundtrip2loss={roundtrip}")


def main():
    print("PROFIT-LOCK trade-off (edge vs give-back), zrev N20/EMA100/D50:\n")
    rep("channel (LIVE)", sim("channel"))
    for a in (1.5, 2.0, 3.0):
        rep(f"breakeven {a}ATR", sim("be", arm=a))
    for a, l in ((2.0, 1.5), (2.0, 3.0), (3.0, 2.0)):
        rep(f"trail arm{a}/lock{l}", sim("trail", arm=a, lock=l))
    print("\n  giveback rendah & roundtrip2loss rendah = profit lebih terlindungi;")
    print("  TAPI lihat OOSpf/net -- kalau turun banyak, itu harga perlindungannya.")


if __name__ == "__main__":
    main()
