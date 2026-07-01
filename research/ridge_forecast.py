"""Ridge-regression forecast of the DEPLOYED book's performance (zrev XAU + US100 orb, 0.01
lot = what the conservative sizing runs now). Two honest uses of Ridge:

  (1) DRIFT: fit weekly PnL and project the equity 26 weeks forward (the central line).
  (2) PREDICTABILITY: fit next-week return ~ [recent returns, rolling vol] with a train/test
      split and report OOS R^2 -- if ~0 the algo's week-to-week path is NOT forecastable, so
      the only honest forecast is 'drift + noise' (a Monte-Carlo cone), not a smooth line.

We overlay a bootstrap MC cone so the point forecast is never mistaken for certainty, and we
compare full-history drift vs last-52w drift (the recent gold trends make the mean optimistic).
Run: python research/ridge_forecast.py
"""
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.linear_model import Ridge
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from zrev_dual_trend import sim_dual, daily_map
from portfolio_audit import nas_dollars

CAP0 = 401.0          # start the forward forecast from the live account
HORIZON_W = 26        # forecast 6 months ahead
OUT = r"C:\Users\ADMINI~1\AppData\Local\Temp\1\claude\C--Users-Administrator\91e0ccf1-c993-48f2-8268-f1678ad108cb\scratchpad\ridge_forecast.png"


def main():
    dmap = daily_map(50)
    z = sim_dual(dmap=dmap, use_daily=True)
    zser = pd.Series([t[3] for t in z], index=pd.DatetimeIndex([t[1] for t in z]))
    nas = nas_dollars()
    book = pd.concat([zser, nas]).sort_index()
    wk = book.resample("W").sum()
    wk = wk[wk != 0].reindex(wk.index).fillna(0.0)     # keep calendar weeks, 0 when no trade
    n = len(wk)
    print(f"book: {len(book)} trades, {n} weeks ({wk.index[0].date()} -> {wk.index[-1].date()}), "
          f"$ at 0.01 lot")

    # (1) Ridge drift: weekly PnL ~ time index (standardized). R^2 ~0 => no time trend.
    weeks = np.arange(n).reshape(-1, 1).astype(float)
    Xs = (weeks - weeks.mean()) / weeks.std()
    r1 = Ridge(alpha=1.0).fit(Xs, wk.values)
    r2_time = r1.score(Xs, wk.values)
    drift_full = float(wk.mean())
    drift_recent = float(wk.iloc[-52:].mean())

    # (2) Ridge predictability: next-week PnL ~ [lag1,lag2,ma4,vol4], train/test OOS R^2.
    d = pd.DataFrame({"y": wk.values})
    d["lag1"] = d["y"].shift(1); d["lag2"] = d["y"].shift(2)
    d["ma4"] = d["y"].shift(1).rolling(4).mean(); d["vol4"] = d["y"].shift(1).rolling(4).std()
    d = d.dropna()
    tr = int(len(d) * 0.7)
    cols = ["lag1", "lag2", "ma4", "vol4"]
    Xtr, ytr = d[cols].values[:tr], d["y"].values[:tr]
    Xte, yte = d[cols].values[tr:], d["y"].values[tr:]
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    r2 = Ridge(alpha=1.0).fit((Xtr - mu) / sd, ytr)
    oos_r2 = r2.score((Xte - mu) / sd, yte)

    # Forecast: Ridge drift line + MC bootstrap cone
    last = CAP0
    fut = pd.date_range(wk.index[-1], periods=HORIZON_W + 1, freq="W")[1:]
    ridge_line = last + np.cumsum(np.full(HORIZON_W, drift_full))
    ridge_recent = last + np.cumsum(np.full(HORIZON_W, drift_recent))
    rng = np.random.default_rng(7)
    sims = np.array([last + np.cumsum(rng.choice(wk.values, HORIZON_W, replace=True))
                     for _ in range(4000)])
    p5, p50, p95 = np.percentile(sims, [5, 50, 95], axis=0)

    hist_eq = CAP0 + wk.cumsum() - wk.cumsum().iloc[-1]     # anchor hist end at CAP0

    print(f"\nRIDGE (1) weekly-PnL vs time  R^2 = {r2_time:+.3f}   (near 0 => no time trend)")
    print(f"RIDGE (2) next-week PnL, OOS  R^2 = {oos_r2:+.3f}   (<=0 => NOT predictable)")
    print(f"\ndrift full-history = ${drift_full:+.2f}/wk  |  last-52w = ${drift_recent:+.2f}/wk "
          f"(recent gold trends -> optimistic)")
    print(f"\nForecast {HORIZON_W}w from ${CAP0:.0f} (0.01 lot):")
    print(f"  Ridge drift line (full)  -> ${ridge_line[-1]:.0f}  ({100*(ridge_line[-1]/CAP0-1):+.0f}%)")
    print(f"  MC median  -> ${p50[-1]:.0f} | MC 5th -> ${p5[-1]:.0f} | MC 95th -> ${p95[-1]:.0f}")
    print(f"  => plan by the LOWER cone (${p5[-1]:.0f}), not the point forecast.")

    plt.figure(figsize=(11, 5.6))
    plt.plot(wk.index, hist_eq.values, color="#333", lw=1.3, label="historis (0.01 lot, di-anchor ke $401)")
    plt.fill_between(fut, p5, p95, color="#1f77b4", alpha=0.15, label="MC 5-95% (ketidakpastian nyata)")
    plt.plot(fut, p50, color="#1f77b4", lw=1.4, ls="-", label=f"MC median ${p50[-1]:.0f}")
    plt.plot(fut, ridge_line, color="#d4a017", lw=1.8, ls="--", label=f"Ridge drift full ${ridge_line[-1]:.0f}")
    plt.plot(fut, ridge_recent, color="#c0392b", lw=1.3, ls=":", label=f"Ridge drift 52w ${ridge_recent[-1]:.0f}")
    plt.axvline(wk.index[-1], color="gray", ls=":", lw=0.7)
    plt.title(f"Ridge forecast performa algo (zrev+US100, 0.01 lot)  "
              f"| predictability OOS R2={oos_r2:+.2f} (~0 = drift+noise)")
    plt.ylabel("Equity ($)"); plt.legend(loc="upper left", fontsize=8); plt.grid(alpha=0.3)
    plt.tight_layout(); plt.savefig(OUT, dpi=110)
    print("\nsaved:", OUT)


if __name__ == "__main__":
    main()
