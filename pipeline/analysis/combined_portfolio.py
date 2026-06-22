"""Combined 'best edges' portfolio backtest + equity curve.

Each asset class runs the strategy that fits it (matched to its nature):
  - NAS100 : ORB breakout + range-filter, RR 1:1   (trending)
  - XAUUSD : ORB breakout + range-filter, RR 1:1   (trending)
  - FX 7 majors : Bollinger reversion, RR 1.5       (mean-reverting)
All at 0.01 lot. Combines every trade chronologically into one book, reports
IS/OOS/ALL stats + per-component contribution, and saves an equity-curve PNG.

    python -m pipeline.analysis.combined_portfolio
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .strategy_lab import load_m1, resample, simulate, s_bb_revert, ROOT
from .orb_refine import orb_entries

MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"]
PIP = {"USDJPY": 0.01}
UPP = {"EURUSD": 0.10, "GBPUSD": 0.10, "AUDUSD": 0.10, "NZDUSD": 0.10,
       "USDCHF": 0.11, "USDCAD": 0.075, "USDJPY": 0.067,
       "NAS100": 0.10, "XAUUSD": 1.0}


def orb_trades(sym):
    m1 = load_m1(sym)
    ent = orb_entries(m1, "orb", use_trend=False, use_range=True)
    slb = {p: sl for p, d, sl in ent}
    r = simulate(m1, ent, 1440, 1.0)
    r["ts"] = m1.index[r["pos"].values]; r["R"] = r["pos"].map(slb)
    r["usd"] = r["pnl_R"] * r["R"] * UPP[sym]; r["src"] = sym
    return r[["ts", "src", "pnl_R", "usd"]]


def bb_trades(sym):
    m1 = load_m1(sym); m15 = resample(m1, "15min")
    df_tf, ent, hz = s_bb_revert(m15)
    slb = {p: sl for p, d, sl in ent}
    r = simulate(df_tf, ent, hz, 1.5)
    if r.empty:
        return r
    r["ts"] = df_tf.index[r["pos"].values]; r["R"] = r["pos"].map(slb)
    r["usd"] = r["pnl_R"] * r["R"] / PIP.get(sym, 0.0001) * UPP[sym]; r["src"] = "FX:" + sym
    return r[["ts", "src", "pnl_R", "usd"]]


def stats(r, label, months):
    if r.empty:
        return {"book": label, "trades": 0}
    pr = r["pnl_R"]; eqR = pr.cumsum(); ddR = (eqR - eqR.cummax()).min()
    rs = r.sort_values("ts"); ue = rs["usd"].cumsum(); udd = (ue - ue.cummax()).min()
    pos, neg = pr[pr > 0].sum(), pr[pr < 0].sum()
    return {"book": label, "trades": len(r), "win%": round(100 * (pr > 0).mean(), 1),
            "PF": round(pos / abs(neg), 2) if neg < 0 else 99,
            "total_R": round(pr.sum(), 1), "maxDD_R": round(ddR, 1),
            "$/mo@0.01": round(r["usd"].sum() / months, 1), "maxDD_$": round(udd, 0)}


def main():
    parts = [orb_trades("NAS100"), orb_trades("XAUUSD")] + [bb_trades(s) for s in MAJORS]
    book = pd.concat([p for p in parts if not p.empty]).sort_values("ts").reset_index(drop=True)

    wins = {"IS": ("2020-01-01", "2023-12-31", 48.0),
            "OOS": ("2024-01-01", "2026-06-10", 29.5),
            "ALL": ("2020-01-01", "2026-06-10", 77.5)}
    for wl, (s, e, mo) in wins.items():
        sub = book[(book["ts"] >= pd.Timestamp(s, tz="UTC")) & (book["ts"] < pd.Timestamp(e, tz="UTC"))]
        print(f"\n===== {wl} {s}..{e} =====")
        comp = []
        for grp in ["NAS100", "XAUUSD"]:
            comp.append(stats(sub[sub["src"] == grp], grp, mo))
        comp.append(stats(sub[sub["src"].str.startswith("FX:")], "FX majors (BB-MR)", mo))
        comp.append(stats(sub, "** COMBINED **", mo))
        print(pd.DataFrame(comp).to_string(index=False))
        if wl == "ALL":
            sub2 = sub.copy()
            sub2["year"] = sub2["ts"].dt.year
            yr = sub2.groupby("year")["usd"].sum().round(0)
            print("\n  $/year (0.01 lot each):"); print("   " + yr.to_string().replace("\n", "\n   "))

    # equity curve PNG
    b = book.sort_values("ts")
    fig, ax = plt.subplots(2, 1, figsize=(11, 8), sharex=True)
    ax[0].plot(b["ts"], b["usd"].cumsum(), color="navy")
    ax[0].set_title("Combined best-edges portfolio — cumulative $ (0.01 lot each)")
    ax[0].axhline(0, color="grey", lw=.6); ax[0].grid(alpha=.3)
    for grp, lab in [("NAS100", "NAS100 ORB"), ("XAUUSD", "XAU ORB")]:
        g = b[b["src"] == grp]; ax[1].plot(g["ts"], g["usd"].cumsum(), label=lab)
    fx = b[b["src"].str.startswith("FX:")]
    ax[1].plot(fx["ts"], fx["usd"].cumsum(), label="FX majors BB-MR")
    ax[1].set_title("Per-component cumulative $"); ax[1].legend(); ax[1].grid(alpha=.3)
    ax[1].axhline(0, color="grey", lw=.6)
    out = ROOT / "_DOC" / "best_portfolio_equity.png"
    fig.tight_layout(); fig.savefig(out, dpi=110); print(f"\nEquity curve saved -> {out}")


if __name__ == "__main__":
    main()
