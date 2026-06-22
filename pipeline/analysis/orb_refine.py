"""Refinement: strengthen the breakout @ RR~1 edge on NAS100 + XAUUSD.

Takes the winners from strategy_lab (ORB breakout, PDH/PDL breakout) and tries to
lift profit-factor while keeping win-rate > 50%, by sweeping RR finely and adding
optional filters:
  - trend : only take longs above prior-day close / shorts below (trade daily bias)
  - range : only take if the opening-range size is 0.5x-1.5x its 20-day median
            (skip abnormally tiny/huge ranges)

    python -m pipeline.analysis.orb_refine
"""
import itertools
import numpy as np
import pandas as pd

from .strategy_lab import load_m1, simulate, metrics, ROOT

SYMBOLS = ["NAS100", "XAUUSD"]
RR_GRID = [1.0, 1.25, 1.5]
HORIZON = 1440


def orb_entries(m1, kind, use_trend, use_range):
    """kind: 'orb' (NY 30m opening range) or 'pdh' (prior-day high/low)."""
    daily = m1.resample("1D").agg({"high": "max", "low": "min", "close": "last"})
    pdc = daily["close"].shift()
    pdh = daily["high"].shift(); pdl = daily["low"].shift()
    sizes = {}  # date -> range size (for median filter, orb only)
    rows = []
    # precompute 20-day median of ORB size if needed
    med = None
    if use_range and kind == "orb":
        tmp = []
        for date, day in m1.groupby(m1.index.date):
            start = pd.Timestamp(str(date), tz="UTC").replace(hour=13, minute=30)
            w = day[(day.index >= start) & (day.index < start + pd.Timedelta(minutes=30))]
            tmp.append((pd.Timestamp(str(date), tz="UTC"), (w["high"].max() - w["low"].min()) if len(w) >= 15 else np.nan))
        s = pd.Series(dict(tmp)).sort_index()
        med = s.rolling(20, min_periods=10).median().shift()

    for date, day in m1.groupby(m1.index.date):
        key = pd.Timestamp(str(date), tz="UTC")
        start = key.replace(hour=13, minute=30)
        send = key.replace(hour=20)

        if kind == "orb":
            rend = start + pd.Timedelta(minutes=30)
            win = day[(day.index >= start) & (day.index < rend)]
            if len(win) < 15: continue
            hi, lo = win["high"].max(), win["low"].min(); size = hi - lo
            post = day[(day.index >= rend) & (day.index <= send)]
        else:  # pdh
            if key not in pdh.index or np.isnan(pdh.loc[key]): continue
            hi, lo = pdh.loc[key], pdl.loc[key]; size = (hi - lo) * 0.5
            post = day[(day.index >= start) & (day.index <= send)]
        if size <= 0 or post.empty: continue

        if use_range and kind == "orb":
            if med is None or key not in med.index or np.isnan(med.loc[key]): continue
            if not (0.5 * med.loc[key] <= size <= 1.5 * med.loc[key]): continue

        for ts, b in post.iterrows():
            d = 1 if b["high"] > hi else (-1 if b["low"] < lo else 0)
            if not d: continue
            entry = hi if d > 0 else lo
            if use_trend and key in pdc.index and not np.isnan(pdc.loc[key]):
                if (d > 0 and entry <= pdc.loc[key]) or (d < 0 and entry >= pdc.loc[key]):
                    break   # breakout against daily bias -> skip the day
            rows.append((m1.index.get_loc(ts), d, size))
            break
    return rows


def main():
    windows = {"IS": ("2020-01-01", "2023-12-31"), "OOS": ("2024-01-01", "2026-06-08")}
    variants = list(itertools.product(["orb", "pdh"], [False, True], [False, True]))
    out = []
    for sym in SYMBOLS:
        print(f"\n##### {sym} #####", flush=True)
        m1 = load_m1(sym)
        for kind, use_trend, use_range in variants:
            if use_range and kind == "pdh":   # range filter only defined for orb
                continue
            ent = orb_entries(m1, kind, use_trend, use_range)
            fname = f"{kind}{'+trend' if use_trend else ''}{'+range' if use_range else ''}"
            print(f"  {sym} {fname}: {len(ent)} entries", flush=True)
            for wlabel, (s, e) in windows.items():
                lo = m1.index.searchsorted(pd.Timestamp(s, tz="UTC"))
                hi = m1.index.searchsorted(pd.Timestamp(e, tz="UTC"))
                sub = [(p, d, sl) for p, d, sl in ent if lo <= p < hi]
                for rr in RR_GRID:
                    m = metrics(simulate(m1, sub, HORIZON, rr))
                    m.update({"symbol": sym, "variant": fname, "rr": rr, "window": wlabel})
                    out.append(m)
    res = pd.DataFrame(out)
    res.to_csv(ROOT / "_DOC" / "orb_refine_results.csv", index=False)

    p = res.pivot_table(index=["symbol", "variant", "rr"], columns="window",
                        values=["win%", "exp_R", "pf", "trades", "maxDD_R"]).reset_index()
    p.columns = [a if not b else f"{a}_{b}" for a, b in p.columns]
    p["min_win"] = p[["win%_IS", "win%_OOS"]].min(axis=1)
    p["min_exp"] = p[["exp_R_IS", "exp_R_OOS"]].min(axis=1)
    show = ["symbol", "variant", "rr", "win%_IS", "win%_OOS", "exp_R_IS", "exp_R_OOS",
            "pf_IS", "pf_OOS", "trades_OOS", "maxDD_R_OOS"]

    print("\n\n===== WR>50% BOTH windows AND profit BOTH (ranked by min_exp) =====", flush=True)
    g = p[(p["min_win"] > 50) & (p["min_exp"] > 0) & (p["trades_OOS"] >= 50)].sort_values("min_exp", ascending=False)
    print(g[show].to_string(index=False) if not g.empty else "  (none)")

    print("\n\n===== Best by OOS profit-factor (WR>50% both) =====", flush=True)
    h = p[(p["min_win"] > 50) & (p["trades_OOS"] >= 50)].sort_values("pf_OOS", ascending=False)
    print(h[show].head(15).to_string(index=False) if not h.empty else "  (none)")


if __name__ == "__main__":
    main()
