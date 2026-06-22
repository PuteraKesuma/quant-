"""Walk-forward validation of the live NAS100 strategy (orb30_nas).

Rolling folds: train 12 months -> pick the RR that maximised in-sample edge ->
apply UNSEEN to the next 6 months (out-of-sample), then roll forward 6 months.
This proves the edge holds period-by-period and that the chosen RR is stable
(not one lucky split). range_minutes=30 + range_filter fixed (the live rule).

    python -m pipeline.analysis.wfo_nas
"""
import numpy as np
import pandas as pd

from .strategy_lab import load_m1, simulate
from .orb_refine import orb_entries

RR_GRID = [0.75, 1.0, 1.25, 1.5]


def main():
    m1 = load_m1("NAS100")
    ent = orb_entries(m1, "orb", use_trend=False, use_range=True)   # live rule, range 30 + filter
    ts = m1.index[[p for p, d, s in ent]]
    ent = list(ent)

    def slice_window(s, e):
        return [ent[i] for i in range(len(ent)) if s <= ts[i] < e]

    def m(sub, rr):
        r = simulate(m1, sub, 1440, rr)
        if r.empty:
            return 0, 0, 0.0
        pr = r["pnl_R"]; pos, neg = pr[pr > 0].sum(), pr[pr < 0].sum()
        return round(pr.sum(), 1), round(100 * (pr > 0).mean(), 0), (round(pos / abs(neg), 2) if neg < 0 else 9.9)

    folds = []
    start = pd.Timestamp("2020-01-01", tz="UTC")
    while start + pd.DateOffset(months=18) <= pd.Timestamp("2026-06-10", tz="UTC"):
        is_s, is_e = start, start + pd.DateOffset(months=12)
        oos_s, oos_e = is_e, is_e + pd.DateOffset(months=6)
        is_sub = slice_window(is_s, is_e)
        oos_sub = slice_window(oos_s, oos_e)
        # pick RR by best in-sample total_R
        best_rr = max(RR_GRID, key=lambda rr: m(is_sub, rr)[0])
        o_R, o_win, o_pf = m(oos_sub, best_rr)
        folds.append({
            "OOS_period": f"{oos_s:%Y-%m}..{oos_e:%Y-%m}",
            "best_RR(IS)": best_rr, "OOS_trades": len(oos_sub),
            "OOS_win%": o_win, "OOS_R": o_R, "OOS_PF": o_pf,
        })
        start += pd.DateOffset(months=6)

    df = pd.DataFrame(folds)
    print("===== WALK-FORWARD: NAS100 orb30_nas (train 12m -> test 6m, roll 6m) =====")
    print(df.to_string(index=False))
    win_folds = (df["OOS_R"] > 0).mean() * 100
    print(f"\n  OOS folds profitable: {win_folds:.0f}%  ({(df['OOS_R']>0).sum()}/{len(df)})")
    print(f"  total OOS R (stitched walk-forward): {df['OOS_R'].sum():.1f}")
    print(f"  RR chosen each fold: {sorted(df['best_RR(IS)'].unique())}  (stable if mostly the same)")
    print(f"  avg OOS PF: {df['OOS_PF'].replace(9.9, np.nan).mean():.2f}")


if __name__ == "__main__":
    main()
