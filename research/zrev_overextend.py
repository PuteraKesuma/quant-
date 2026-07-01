"""Test an 'over-extension' entry filter for zrev: do NOT chase a breakout that is already
stretched far from the mean/channel (the '05:05 sold-the-exact-bottom' whipsaw). Skip a
SHORT if price at entry is already > K*ATR below the reference; skip a LONG if > K*ATR above.

Two references:
  ref='ema'     : distance from the H1 EMA100 (how stretched from the mean)
  ref='channel' : distance from the far 20-bar band (how far it has already broken)

Faithful to LIVE: entry_n=exit_n=20, EMA100 + Daily SMA50 filters, hard ATR-stop mult=3.0
(tighter of the entry+/-3*ATR hard stop and the trailing channel band), cost 0.30.

Honest bar: the filter WINS only if OOS PF/net hold or improve AND maxDD improves, without
gutting the trade count / trend entries. Also report the choppy window (27Mar-14May) net.
Run: python research/zrev_overextend.py
"""
import sys
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, to_d1, stats, split, mc_pf_p5, per_year
from zrev_dual_trend import daily_map

XAU = load_m1("XAUUSD")
H1 = (XAU.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
         .dropna(subset=["open"]))
CHOP0, CHOP1 = pd.Timestamp("2026-03-27", tz="UTC"), pd.Timestamp("2026-05-14", tz="UTC")


def _atr(n=14):
    tr = pd.concat([H1["high"] - H1["low"], (H1["high"] - H1["close"].shift()).abs(),
                    (H1["low"] - H1["close"].shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def sim(K=None, ref="ema", mult=3.0, N=20, ema_n=100, cost=0.30, dmap=None):
    O = H1["open"].values; Hi = H1["high"].values; Lo = H1["low"].values
    up = H1["high"].rolling(N).max().shift(1).values
    lo = H1["low"].rolling(N).min().shift(1).values
    emaS = H1["close"].ewm(span=ema_n, adjust=False).mean()
    h1_up = (H1["close"] > emaS).shift(1).values
    emaV = emaS.shift(1).values
    atr = _atr(14).shift(1).values
    dates = H1.index.date; idx = H1.index
    trades = []; pos = 0; ep = ets = astop = None; skipped = 0

    def over_short(i, f):
        if K is None:
            return False
        e = (up[i] - f) if ref == "channel" else (emaV[i] - f)
        return e / atr[i] > K

    def over_long(i, f):
        if K is None:
            return False
        e = (f - lo[i]) if ref == "channel" else (f - emaV[i])
        return e / atr[i] > K

    for i in range(len(H1)):
        if np.isnan(up[i]) or np.isnan(lo[i]) or np.isnan(atr[i]) or (
                isinstance(h1_up[i], float) and np.isnan(h1_up[i])):
            continue
        dt = dmap.get(dates[i], 0) if dmap else 0
        can_long = bool(h1_up[i]) and dt == 1
        can_short = (not bool(h1_up[i])) and dt == -1
        if pos == 0:
            if Hi[i] >= up[i] and can_long:
                f = max(O[i], up[i])
                if over_long(i, f):
                    skipped += 1; continue
                pos, ep, ets, astop = 1, f, idx[i], f - mult * atr[i]
            elif Lo[i] <= lo[i] and can_short:
                f = min(O[i], lo[i])
                if over_short(i, f):
                    skipped += 1; continue
                pos, ep, ets, astop = -1, f, idx[i], f + mult * atr[i]
            continue
        if pos == 1:
            stop = max(astop, lo[i])                       # trailing tighter of ATR-stop / channel
            if Lo[i] <= stop:
                is_rev = lo[i] >= astop                    # channel band binding -> reverse
                fill = min(O[i], lo[i] if is_rev else astop)
                trades.append((ets, idx[i], "long", (fill - ep) - cost))
                if is_rev and Lo[i] <= lo[i] and can_short and not over_short(i, min(O[i], lo[i])):
                    pos, ep, ets, astop = -1, min(O[i], lo[i]), idx[i], min(O[i], lo[i]) + mult * atr[i]
                else:
                    pos = 0
        else:
            stop = min(astop, up[i])
            if Hi[i] >= stop:
                is_rev = up[i] <= astop
                fill = max(O[i], up[i] if is_rev else astop)
                trades.append((ets, idx[i], "short", (ep - fill) - cost))
                if is_rev and Hi[i] >= up[i] and can_long and not over_long(i, max(O[i], up[i])):
                    pos, ep, ets, astop = 1, max(O[i], up[i]), idx[i], max(O[i], up[i]) - mult * atr[i]
                else:
                    pos = 0
    return trades, skipped


def rep(tag, res):
    trades, skipped = res
    items = [(t[1], t[3]) for t in trades]
    _, o = split(items)
    pnl = np.array([p for _, p in items]); eq = np.cumsum(pnl)
    mdd = (eq - np.maximum.accumulate(eq)).min()
    chop = sum(p for ts, p in items if CHOP0 <= ts <= CHOP1)
    so = stats(o); pf = "inf" if so["pf"] == float("inf") else f"{so['pf']:.2f}"
    py = per_year(items); g = sum(1 for v in py.values() if v[0] >= 1.0)
    print(f"  {tag:22s} n={len(items):4d} skip={skipped:3d} OOSpf={pf:>4} net=${pnl.sum():+6.0f} "
          f"maxDD=${mdd:+6.0f} MC={mc_pf_p5(o):.2f} grn={g}/{len(py)} chop=${chop:+5.0f}")


def main():
    dmap = daily_map(50)
    print("zrev + hard ATR-stop 3x. Over-extension entry filter (skip stretched breakouts).")
    print("CHOP window 27Mar-14May. WIN only if OOSpf/net hold & maxDD improves, trades not gutted.\n")
    print("BASELINE (no filter, = live sekarang):")
    rep("baseline", sim(K=None, dmap=dmap))
    for ref in ("ema", "channel"):
        print(f"\nref='{ref}' (skip if entry > K*ATR from {'EMA100' if ref=='ema' else 'far band'}):")
        for K in (2.5, 3.0, 3.5, 4.0, 5.0):
            rep(f"K={K}", sim(K=K, ref=ref, dmap=dmap))


if __name__ == "__main__":
    main()
