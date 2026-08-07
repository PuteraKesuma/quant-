"""FINAL pre-$1000 config. Two of my own errors to correct here:

1. I picked Z's ADX gate on the criterion "Z-ONLY 2021 >= 0" -> forced gate 28. Wrong objective: the
   BOOK only needs the BOOK green. ORB+Reversal already carry 2021 (+31 +288 = +319), so Z only needs
   to lose less than that. Gate 28 pays ~$2400 of profit to flip a number that didn't need flipping.
2. Judge on the real thing: path-dependent equity from $1000 (DD% against equity AT THE TIME), not
   worst-year $DD / starting equity.

Golden is OFF here: its edge is a 55-min H1 lookahead (research reindexes H1 gates onto M5 with ffill
and no shift). Honest PF 1.04, 3/6 green. It doubles book DD for +$361. See research/golden_check.py.

Sweep the gate on BOOK-level green + Sharpe + maxDD at $1000, and pick on robustness, not on one year.
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from regime_fix import accurate_z_usd, orb_usd, reversal_usd, daily

EQ0 = 1000.0
xau = load_m1("XAUUSD"); nas = load_m1("NAS100")
O = daily(orb_usd(nas)); R = daily(reversal_usd("NAS100"))


def simulate(base, cal, step=1500.0, eq0=EQ0):
    b = base.values.astype(float); eq = eq0; curve = np.empty(len(cal))
    for i in range(len(cal)):
        k = 1 if step is None else max(1, int(eq // step))
        eq += b[i] * k; curve[i] = eq
    return pd.Series(curve, index=cal)


def stats(curve, cal):
    dd = (curve - curve.cummax()) / curve.cummax()
    ret = curve.pct_change().fillna(0)
    greens = tot = 0; wyr = 0.0
    for y in sorted(set(cal.year)):
        c = curve[cal.year == y]
        if len(c) < 5: continue
        tot += 1; greens += int(c.iloc[-1] > c.iloc[0])
        wyr = min(wyr, float(((c - c.cummax()) / c.cummax()).min()))
    yrs = (cal[-1] - cal[0]).days / 365.25
    return dict(term=curve.iloc[-1], cagr=(curve.iloc[-1] / EQ0) ** (1 / yrs) - 1,
                mdd=float(dd.min()), wyr=wyr, greens=greens, tot=tot,
                sh=ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else np.nan)


print("=" * 104)
print(f"Z ADX-GATE, judged on the BOOK at ${EQ0:,.0f} (compound 1 lot-step per $1500, Golden OFF)")
print("=" * 104)
print(f"{'gate':>5}{'Ztr':>6}{'final$':>10}{'CAGR':>7}{'maxDD':>7}{'worstYr':>8}  green{'Sharpe':>7}"
      f"{'MAR':>6}   per-year book$")
print("-" * 104)
rows = []
for g in [0, 15, 18, 20, 22, 25, 28]:
    ztr = accurate_z_usd(xau, adx_min=g); Z = daily(ztr)
    lo = max(Z.index.min(), O.index.min()); hi = min(Z.index.max(), O.index.max(), R.index.max())
    cal = pd.date_range(lo, hi, freq="B", tz="UTC")
    base = Z.reindex(cal).fillna(0) + O.reindex(cal).fillna(0) + R.reindex(cal).fillna(0)
    cur = simulate(base, cal); s = stats(cur, cal)
    yr = "".join(f"{base[cal.year == y].sum():>+7.0f}" for y in range(2021, 2027))
    mar = s["cagr"] / abs(s["mdd"]) if s["mdd"] else np.nan
    print(f"{g:>5}{len(ztr):>6}{s['term']:>10,.0f}{s['cagr']:>7.0%}{s['mdd']:>7.0%}{s['wyr']:>8.0%}"
          f"  {s['greens']}/{s['tot']}{s['sh']:>7.2f}{mar:>6.2f}   {yr}")
    rows.append((g, s, mar))

print("-" * 104)
print("per-year columns = 2021..2026 book $ at base size. MAR = CAGR / maxDD (higher = better paid per")
print("unit of pain). Pick on green-every-year + Sharpe + MAR, NOT on any single year's sign.")
best = max([r for r in rows if r[1]["greens"] == r[1]["tot"]], key=lambda r: r[2], default=None)
if best:
    g, s, mar = best
    print(f"\nBEST all-green config: ADX gate {g} -> ${s['term']:,.0f} | CAGR {s['cagr']:.0%} | "
          f"maxDD {s['mdd']:.0%} | Sharpe {s['sh']:.2f} | MAR {mar:.2f} | green {s['greens']}/{s['tot']}")
print("DONE")
