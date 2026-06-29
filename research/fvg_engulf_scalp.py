"""M1 FVG + retest + engulfing-confirmation scalp — turning the discretionary idea
into precise, no-lookahead rules and backtesting it (incl. the 'works at session
open' hypothesis).

Rules (all on COMPLETED candles; entry at NEXT bar open):
  - FVG at bar i: bullish if low[i] > high[i-2] (zone = high[i-2]..low[i]); bearish if
    high[i] < low[i-2] (zone = high[i]..low[i-2]). A 3-candle imbalance.
  - The FVG stays active up to `max_age` bars or until invalidated (price closes
    through it).
  - Entry trigger at bar j (flat only): price RETESTS an active FVG (bar j's range
    touches the zone) AND bar j is an ENGULFING candle in the FVG's direction.
  - Enter at open[j+1]. SL beyond the engulfing extreme (+buffer); TP = tp_R * risk.
  - Optional session window (minutes UTC) to test the 'at the open' hypothesis.

Cost = spread+slip in points (FBS XAU ~0.5). PnL in R. IS<2025-01-01<=OOS.
Run: python research/fvg_engulf_scalp.py
"""
import sys
from collections import deque
import numpy as np
import pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, stats, split, mc_pf_p5, per_year


def fvg_engulf(m1, tp_R=2.0, buf=0.05, max_age=60, max_hold=120, window=None, cost=0.5, tmap=None):
    O = m1["open"].values; H = m1["high"].values; L = m1["low"].values; C = m1["close"].values
    mod = m1.index.hour.values * 60 + m1.index.minute.values
    dts = m1.index.date if tmap else None
    ts = m1.index
    n = len(m1)
    active = deque(maxlen=30)            # recent unfilled FVGs: (dir, zbot, ztop, formed_j)
    trades = []
    pos = None                            # (dir, entry, sl, tp, entry_j, risk)
    pending = None                        # setup detected at j -> enter at open[j+1]
    in_win = (lambda m: True) if not window else (lambda m: window[0] <= m < window[1])
    for j in range(2, n):
        # ---- manage open position ----
        if pos is not None:
            d, ent, sl, tp, ej, risk = pos
            cr = cost / risk
            if d == 1:
                if L[j] <= sl: trades.append((ts[j], -1 - cr)); pos = None
                elif H[j] >= tp: trades.append((ts[j], tp_R - cr)); pos = None
            else:
                if H[j] >= sl: trades.append((ts[j], -1 - cr)); pos = None
                elif L[j] <= tp: trades.append((ts[j], tp_R - cr)); pos = None
            if pos is not None and j - ej >= max_hold:
                d, ent, sl, tp, ej, risk = pos
                trades.append((ts[j], (d * (C[j] - ent)) / risk - cost / risk)); pos = None
        # ---- fill a pending entry at this bar's open ----
        if pos is None and pending is not None:
            d, zbot, ztop, eng_low, eng_high = pending
            ent = O[j]
            if d == 1:
                sl = min(eng_low, zbot) - buf; risk = ent - sl
            else:
                sl = max(eng_high, ztop) + buf; risk = sl - ent
            if risk > 0:
                tp = ent + d * tp_R * risk
                pos = (d, ent, sl, tp, j, risk)
            pending = None
        # ---- detect new FVG (bars j-2, j-1, j) ----
        if L[j] > H[j - 2]:
            active.append((1, H[j - 2], L[j], j))
        elif H[j] < L[j - 2]:
            active.append((-1, H[j], L[j - 2], j))
        # ---- look for entry trigger (flat, in-window) ----
        if pos is None and pending is None and in_win(mod[j]):
            bull_eng = C[j] > O[j] and C[j - 1] < O[j - 1] and O[j] <= C[j - 1] and C[j] >= O[j - 1]
            bear_eng = C[j] < O[j] and C[j - 1] > O[j - 1] and O[j] >= C[j - 1] and C[j] <= O[j - 1]
            trend = tmap.get(dts[j], 0) if tmap else 0
            for f in list(active):
                d, zbot, ztop, fj = f
                if fj >= j - 1:
                    continue
                if tmap and d != trend:                 # only trade FVGs WITH the daily trend
                    continue
                if d == 1 and bull_eng and zbot <= L[j] <= ztop:
                    pending = (1, zbot, ztop, L[j], H[j]); active.remove(f); break
                if d == -1 and bear_eng and zbot <= H[j] <= ztop:
                    pending = (-1, zbot, ztop, L[j], H[j]); active.remove(f); break
    return trades


def rep(tag, tr):
    if len(tr) < 20:
        print(f"  {tag:28s} n={len(tr)}"); return
    i_, o = split(tr); pnl = np.array([p for _, p in tr]); wr = 100 * (pnl > 0).mean()
    eq = np.cumsum(pnl); mdd = (eq - np.maximum.accumulate(eq)).min()
    so = stats(o); si = stats(i_)
    pfo = "inf" if so["pf"] == float("inf") else f"{so['pf']:.2f}"
    pfi = "inf" if si["pf"] == float("inf") else f"{si['pf']:.2f}"
    print(f"  {tag:28s} n={len(tr):4d} WR={wr:3.0f}% ISpf={pfi:>4} OOSpf={pfo:>4} "
          f"net={pnl.sum():+6.0f}R maxDD={mdd:6.0f}R MCp5={mc_pf_p5(o):.2f}")


def main():
    xau = load_m1("XAUUSD")
    print(f"XAU M1 {len(xau):,}  (PnL in R; cost 0.5pt; IS/OOS @2025-01-01)\n")
    WIN = {"all-day": None, "London-open 08-10": (480, 600), "NY-open 13:30-15:30": (810, 930),
           "London+NY opens": None}
    for tp in (1.5, 2.0, 3.0):
        print(f"--- tp_R={tp} ---")
        rep(f"all-day tp{tp}",   fvg_engulf(xau, tp_R=tp))
        rep(f"London-open tp{tp}", fvg_engulf(xau, tp_R=tp, window=(480, 600)))
        rep(f"NY-open tp{tp}",   fvg_engulf(xau, tp_R=tp, window=(810, 930)))


if __name__ == "__main__":
    main()
