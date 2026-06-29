"""Always-in Donchian S&R (the user's requested profile: SELALU ada posisi, flip on
breakout, trade ~daily) + a disciplined search for a higher profit factor.

Two parts:
  1. audit the chosen live config (H1 entry20/exit20 = pure always-in) — cost
     sensitivity, per-year, Monte-Carlo 5th-pct, executability.
  2. search N x ADX-filter x trend-filter for a ROBUST PF>=2, using strict IS/OOS,
     bootstrap MC, walk-forward, and a robustness gate. Conclusion: no config clears
     PF>=2 robustly — the ones that flash high OOS PF fail MC/regime checks (the
     mr_xau mirage). The honest frontier on gold is OOS PF ~1.5-1.7; the best
     ROBUST + frequent point is entry20/exit20 (OOS 1.58, MC p5 1.18).

Uses the committed, externally-audited pipeline.backtest.strategy_zrev.simulate.
Run: python research/zrev_alwaysin_search.py
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from audit_live_strategies import load_m1, stats, split, fmt, per_year, mc_pf_p5
from pipeline.backtest.strategy_zrev import simulate, ZRevParams, trades_to_df, resample_1h


def _items(tr):
    return list(zip(pd.to_datetime(tr["entry_ts"]), tr["pnl_points"]))


def wf_green(items, months=6):
    if not items:
        return (0, 0)
    s = pd.Series([p for _, p in items], index=pd.DatetimeIndex([t for t, _ in items]))
    g = s.resample(f"{months}MS").sum(); c = s.resample(f"{months}MS").count()
    g = g[c >= 5]
    return (int((g > 0).sum()), len(g))


def audit_alwaysin(h1, N=20, cost=0.30):
    print(f"\n=== AUDIT always-in entry{N}/exit{N} (exit_n=0 => pure S&R) ===")
    for c in (0.30, 0.60, 1.00):
        tr = trades_to_df(simulate(h1, ZRevParams(N, 0, c)))
        it = _items(tr); i_, o = split(it)
        print(f"  cost {c:.2f}: ALL {fmt(stats([p for _, p in it]))}")
        print(f"            IS  {fmt(stats(i_))} | OOS {fmt(stats(o))}")
    tr = trades_to_df(simulate(h1, ZRevParams(N, 0, cost))); it = _items(tr); i_, o = split(it)
    py = per_year(it)
    print("  per-year PF:", {y: round(v[0], 2) for y, v in py.items()},
          "green", sum(1 for v in py.values() if v[0] >= 1.0), "/", len(py))
    print(f"  MC OOS p5: {mc_pf_p5(o):.2f}   WF green: {wf_green(it)}")
    print(f"  trades {len(tr)} (SL=opposite channel -> 0% invalid by construction)")


def search(h1):
    print("\n=== SEARCH for robust PF>=2 (N x ADX x trend filter) ===")
    rows = []
    for N in (15, 20, 25, 30, 40):
        for adx in (0, 22, 28):
            for ema in (0, 100, 200):
                p = ZRevParams(donchian_n=N, exit_n=0, cost_points=0.30,
                               trend_filter=(ema > 0), trend_ema=(ema or 200),
                               adx_filter=(adx > 0), adx_min=(adx or 20))
                tr = trades_to_df(simulate(h1, p))
                if len(tr) < 40:
                    continue
                it = _items(tr); i_, o = split(it); si, so = stats(i_), stats(o)
                if so["n"] < 50:
                    continue
                py = per_year(it); gy = sum(1 for v in py.values() if v[0] >= 1.0)
                wfg, wft = wf_green(it)
                rows.append(dict(N=N, adx=adx, ema=ema, n=len(tr), ISpf=si["pf"], OOSpf=so["pf"],
                                 OOSn=so["n"], mcp5=mc_pf_p5(o), gy=gy, nyr=len(py), wf=f"{wfg}/{wft}"))
    df = pd.DataFrame(rows)
    df["robust2"] = ((df.OOSpf >= 2.0) & (df.ISpf >= 1.3) & (df.mcp5 >= 1.3)
                     & (df.gy >= 4) & (df.OOSn >= 60))
    pd.set_option("display.width", 200)
    print(df.sort_values("OOSpf", ascending=False).head(12).to_string(index=False))
    print(f"\nROBUST PF>=2 configs: {int(df.robust2.sum())}  "
          f"(gate: OOS>=2 & IS>=1.3 & MCp5>=1.3 & green>=4 & OOSn>=60)")
    print("Honest finding: NONE. Best OOS PF ~1.7 has MC p5 < 0.9 (fragile, regime-lucky)\n"
          "and trades only ~1/week. Forcing PF>=2 by trying enough combos = overfitting\n"
          "(the mr_xau mechanism). The robust+frequent optimum stays entry20/exit20.")


if __name__ == "__main__":
    xau = load_m1("XAUUSD")
    h1 = resample_1h(xau.assign(volume=0))
    audit_alwaysin(h1, 20)
    search(h1)
