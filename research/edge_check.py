"""Edge re-check for the CURRENT live XAU/NAS book: Z (XAU) + ORB (NAS). Sharpe, PF, win%, maxDD,
per-year, bootstrap 95% CI. Z via committed strategy_zrev w/ live-ish params (entry20/EMA100 trend) —
APPROX (excludes live daily-filter + 3xATR stop + dynamic lot). ORB via nas_orb — EXACT live logic.
Golden not measurable (no verified backtest). Backtest over the M1 span (2021-2026); the account has no
long LIVE track record yet (funded 2026-06-24).

Run: python research/edge_check.py
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1, nas_orb
from pipeline.backtest.strategy_zrev import simulate as zrev_sim, ZRevParams, resample_1h

np.random.seed(7)


def metrics(items, name, note=""):
    """items = [(exit_ts, pnl)] in strategy-native units (Sharpe/PF/DD are scale-invariant or self-consistent)."""
    pnl = pd.Series([p for _, p in items], index=pd.DatetimeIndex([t for t, _ in items], tz="UTC")).sort_index()
    n = len(pnl); wins = pnl[pnl > 0]; loss = pnl[pnl < 0]
    pf = wins.sum() / abs(loss.sum()) if loss.sum() != 0 else float("inf")
    wr = len(wins) / n
    eq = pnl.cumsum(); dd_pts = (eq - eq.cummax()).min()          # max drawdown in native units
    dd_pct = ((eq - eq.cummax()) / (eq.cummax().replace(0, np.nan))).min()
    # daily series (0-fill non-trade days) for an annualized Sharpe
    day = pnl.groupby(pnl.index.normalize()).sum()
    cal = pd.date_range(day.index.min(), day.index.max(), freq="B", tz="UTC")
    d = day.reindex(cal).fillna(0.0)
    sh = d.mean() / d.std() * np.sqrt(252) if d.std() > 0 else np.nan
    # bootstrap CI on Sharpe (block)
    a = d.values; N = len(a); nb = max(1, N // 10); shs = []
    for _ in range(3000):
        idx = (np.random.randint(0, N - 10, nb)[:, None] + np.arange(10)).ravel()
        s = a[idx]; sd = s.std(); shs.append(s.mean() / sd * np.sqrt(252) if sd > 0 else 0.0)
    lo, hi = np.percentile(shs, [2.5, 97.5])
    yr = pnl.groupby(pnl.index.year).sum(); grn = int((yr > 0).sum())
    print(f"=== {name} ===  {note}")
    print(f"  trades {n}  win% {wr:.0%}  PF {pf:.2f}  Sharpe {sh:+.2f}  95%CI[{lo:+.2f},{hi:+.2f}]  "
          f"maxDD {dd_pct:.0%}  yrs+ {grn}/{len(yr)}")
    print(f"  per-year PnL: " + "  ".join(f"{y}:{v:+.0f}" for y, v in yr.items()))
    return d


# Z (XAU) — live-ish config
h1 = resample_1h(load_m1("XAUUSD").assign(volume=0))
ztr = zrev_sim(h1, ZRevParams(donchian_n=20, exit_n=20, trend_filter=True, trend_ema=100, cost_points=0.30))
z_items = [(t.exit_ts, t.pnl_points) for t in ztr if t.exit_ts is not None]
zd = metrics(z_items, "Z (XAU) 1H Donchian S&R + EMA100", "APPROX: no daily-filter/ATR-stop/dynamic-lot")
print()
# ORB (NAS) — exact
o_items = nas_orb(load_m1("NAS100"))[0]
od = metrics(o_items, "ORB (NAS) NY 30m breakout", "EXACT live logic")

# combined (normalize each to unit daily vol, equal-risk)
J = pd.concat([zd.rename("z"), od.rename("o")], axis=1).dropna()
zn = J["z"] / J["z"].std(); on = J["o"] / J["o"].std()
comb = (zn + on) / 2
corr = J["z"].corr(J["o"])
csh = comb.mean() / comb.std() * np.sqrt(252)
print(f"\n=== COMBINED (Z + ORB, equal-risk) ===")
print(f"  Sharpe {csh:+.2f}   corr(Z,ORB) = {corr:+.2f}   (low corr = they diversify each other)")
print("\nNOTE: backtest edge on M1 2021-2026 (recent, gold+equity-favorable). Z is APPROX (live has extra")
print("filters/ATR-stop that change it); Golden not measurable. No long LIVE track record yet.")
print("DONE")
