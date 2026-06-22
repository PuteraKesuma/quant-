"""Backtest a 3-leg scale-out exit for the live XAU ORB entry.

Entry = current live `orb30_xau` trigger (NY 30-min opening-range breakout, both
directions). Compare the current single-TP exit vs a 3-leg scale-out:
  - leg1: TP1   - leg2: TP2   - leg3: runner (trails, no fixed TP)
Rules: initial SL = 1x range (all legs); once the nearest TP (TP1) is reached,
remaining legs move to breakeven and the runner trails `trail` x range behind the
best price; leftovers closed at 20:00 UTC time-exit. PnL summed across legs in R
(R = range). $ uses XAU 0.01 lot/leg ($1 per $1 move), minus rough spread/leg.

    python -m pipeline.analysis.xau_multileg
"""
import pandas as pd

from .strategy_lab import load_m1
from .orb_refine import orb_entries

SYMBOL = "XAUUSD"
USD_PER_PT_001 = 1.0          # XAU 0.01 lot -> $1 per $1.00 move (per leg)
SPREAD_PT = 0.30             # rough round-trip spread per leg (price)


def sim_legs(m1, entries, legs, sl_mult=1.0):
    """legs: list of ('tp', mult) or ('run', trail). Returns per-trade pnl in R."""
    H = m1["high"].values; L = m1["low"].values; C = m1["close"].values
    idx = m1.index
    fixed = [m for k, m in legs if k == "tp"]
    first_tp = min(fixed) if fixed else None
    rows = []
    for pos, d, R in entries:
        entry = C[pos]
        end = int(idx.searchsorted(idx[pos].normalize() + pd.Timedelta(hours=20), side="right"))
        if end <= pos + 1:
            continue
        stops = [entry - sl_mult * R * d for _ in legs]
        openf = [True] * len(legs)
        be = False; peak = entry; pnl = 0.0; n_legs = len(legs)
        for i in range(pos + 1, end):
            hi, lo = H[i], L[i]
            peak = max(peak, hi) if d > 0 else min(peak, lo)
            if be:                                   # trail runner legs
                for k, (kind, par) in enumerate(legs):
                    if kind == "run" and openf[k]:
                        cand = peak - par * R * d
                        stops[k] = max(stops[k], cand) if d > 0 else min(stops[k], cand)
            # stops first (conservative)
            for k in range(n_legs):
                if openf[k] and ((d > 0 and lo <= stops[k]) or (d < 0 and hi >= stops[k])):
                    pnl += (stops[k] - entry) / R * d; openf[k] = False
            # nearest TP -> close that leg, move rest to BE
            if not be and first_tp is not None and ((d > 0 and hi >= entry + first_tp * R) or (d < 0 and lo <= entry - first_tp * R)):
                be = True
                for k, (kind, par) in enumerate(legs):
                    if kind == "tp" and par == first_tp and openf[k]:
                        pnl += first_tp; openf[k] = False
                for k in range(n_legs):
                    if openf[k]:
                        stops[k] = max(stops[k], entry) if d > 0 else min(stops[k], entry)
            # further fixed TPs
            for k, (kind, par) in enumerate(legs):
                if kind == "tp" and par != first_tp and openf[k] and ((d > 0 and hi >= entry + par * R) or (d < 0 and lo <= entry - par * R)):
                    pnl += par; openf[k] = False
            if not any(openf):
                break
        last = C[min(end, len(C)) - 1]
        for k in range(n_legs):
            if openf[k]:
                pnl += (last - entry) / R * d
        rows.append((idx[pos], pnl, R))
    return pd.DataFrame(rows, columns=["ts", "pnl_R", "R"]).set_index("ts")


def metrics(t, label, months, n_legs):
    if t.empty:
        return {"preset": label, "trades": 0}
    r = t["pnl_R"]; eq = r.cumsum(); dd = (eq - eq.cummax()).min()
    pos, neg = r[r > 0].sum(), r[r < 0].sum()
    usd = t["pnl_R"] * t["R"] * USD_PER_PT_001 - n_legs * SPREAD_PT * USD_PER_PT_001
    return {
        "preset": label,
        "trades": len(r),
        "win%": round(100 * (r > 0).mean(), 1),
        "exp_R": round(r.mean(), 3),
        "pf": round(pos / abs(neg), 2) if neg < 0 else float("inf"),
        "total_R": round(r.sum(), 1),
        "maxDD_R": round(dd, 1),
        "net_$/mo": round(usd.sum() / months, 2),
        "maxDD_$": round((usd.cumsum() - usd.cumsum().cummax()).min(), 0),
    }


def main():
    m1 = load_m1(SYMBOL)
    entries = orb_entries(m1, "orb", use_trend=False, use_range=False)   # same trigger as live orb30_xau
    print(f"{SYMBOL}: {len(entries)} ORB entries (NY 30m)  [0.01 lot/leg, spread {SPREAD_PT}/leg]\n")

    presets = [
        ("BASE 1-leg TP3 (live now)", [("tp", 3.0)]),
        ("A 3-leg TP1=1 TP2=2 trail=1", [("tp", 1.0), ("tp", 2.0), ("run", 1.0)]),
        ("B 3-leg TP1=1.5 TP2=3 trail=1.5", [("tp", 1.5), ("tp", 3.0), ("run", 1.5)]),
        ("C 3-leg TP1=2 TP2=4 trail=2", [("tp", 2.0), ("tp", 4.0), ("run", 2.0)]),
    ]
    windows = {"IS 2020-2023": ("2020-01-01", "2023-12-31", 48.0),
               "OOS 2024-2026": ("2024-01-01", "2026-06-08", 29.0)}

    for wlabel, (s, e, mo) in windows.items():
        print(f"===== {SYMBOL}  {wlabel} =====")
        lo = m1.index.searchsorted(pd.Timestamp(s, tz="UTC"))
        hi = m1.index.searchsorted(pd.Timestamp(e, tz="UTC"))
        sub = [(p, d, R) for p, d, R in entries if lo <= p < hi]
        out = [metrics(sim_legs(m1, sub, legs), label, mo, len(legs)) for label, legs in presets]
        print(pd.DataFrame(out).to_string(index=False))
        print()


if __name__ == "__main__":
    main()
