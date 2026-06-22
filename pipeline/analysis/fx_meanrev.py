"""Mean-reversion portfolio test on FX majors (breakout failed there).

Tests strategy types that suit choppy/mean-reverting FX: London-range FADE,
RSI(2) reversion, Bollinger reversion. Same rule on all 7 majors, RR sweep,
report per-strategy portfolio PF/edge over IS & OOS.

    python -m pipeline.analysis.fx_meanrev
"""
import pandas as pd
import numpy as np

from .strategy_lab import (load_m1, resample, simulate,
                           s_orb_break, s_rsi2, s_bb_revert)

MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"]
RR_GRID = [1.0, 1.5]
STRATS = {
    "orb_FADE": ("m1", lambda d: s_orb_break(d, fade=True)),
    "rsi2_revert": ("m15", s_rsi2),
    "bb_revert": ("m15", s_bb_revert),
}


def pf(r):
    pos, neg = r[r > 0].sum(), r[r < 0].sum()
    return round(pos / abs(neg), 2) if neg < 0 else 99.0


def main():
    windows = {"IS": ("2020-01-01", "2023-12-31"), "OOS": ("2024-01-01", "2026-06-10")}
    # cache resamples per symbol
    cache = {}
    for sym in MAJORS:
        m1 = load_m1(sym)
        cache[sym] = {"m1": m1, "m15": resample(m1, "15min")}
        print(f"loaded {sym}", flush=True)

    rows = []
    for sname, (tf, fn) in STRATS.items():
        # precompute entries per symbol once
        ent_by_sym = {}
        for sym in MAJORS:
            df_tf, entries, horizon = fn(cache[sym][tf])
            ent_by_sym[sym] = (df_tf, entries, horizon)
        for rr in RR_GRID:
            for wlabel, (s, e) in windows.items():
                book = []
                for sym in MAJORS:
                    df_tf, entries, horizon = ent_by_sym[sym]
                    lo = df_tf.index.searchsorted(pd.Timestamp(s, tz="UTC"))
                    hi = df_tf.index.searchsorted(pd.Timestamp(e, tz="UTC"))
                    sub = [(p, d, sl) for p, d, sl in entries if lo <= p < hi]
                    res = simulate(df_tf, sub, horizon, rr)
                    if not res.empty:
                        book.append(res["pnl_R"])
                if not book:
                    continue
                r = pd.concat(book, ignore_index=True)
                rows.append({"strategy": sname, "rr": rr, "window": wlabel,
                             "trades": len(r), "win%": round(100 * (r > 0).mean(), 1),
                             "PF": pf(r), "total_R": round(r.sum(), 1),
                             "exp_R": round(r.mean(), 3)})
    res = pd.DataFrame(rows)
    p = res.pivot_table(index=["strategy", "rr"], columns="window",
                        values=["PF", "total_R", "win%", "exp_R"]).reset_index()
    p.columns = [a if not b else f"{a}_{b}" for a, b in p.columns]
    p["min_PF"] = p[["PF_IS", "PF_OOS"]].min(axis=1)
    print("\n===== FX MAJORS portfolio mean-reversion (ranked by min PF IS/OOS) =====")
    cols = ["strategy", "rr", "win%_IS", "win%_OOS", "PF_IS", "PF_OOS", "total_R_IS", "total_R_OOS"]
    print(p.sort_values("min_PF", ascending=False)[cols].to_string(index=False))


if __name__ == "__main__":
    main()
