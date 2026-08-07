"""COMPOUNDING at $1000. The min-lot floor means at $1000 we are FORCED to k=1 (0.01 lot) -> worst-year
$DD ~$323 = 32% of $1000. But compounding changes the question: instead of "what DD does $500 give",
ask "what equity-per-lot-step (STEP) holds DD at 10-12% in steady state, and how do we survive the early
stretch before equity reaches it?"

k = max(1, int(eq // STEP))  -- integer because lot granularity is 0.01 (min lot = step size).
STEP is the real risk knob: bigger STEP = fewer lots per dollar = lower DD% but slower compounding.

Book: Z(XAUUSD, ADX-28 gate) + ORB(NAS100) + Reversal(NAS100). True FBS tick values.
Also tests a trailing DD governor that runs ONLY while equity is below a release level (protect the
fragile early stretch, then let compounding run free).
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from regime_fix import accurate_z_usd, orb_usd, reversal_usd, daily, load_m1

EQ0 = 1000.0
ADX_GATE = 28

xau = load_m1("XAUUSD"); nas = load_m1("NAS100")
Z = daily(accurate_z_usd(xau, adx_min=ADX_GATE))
O = daily(orb_usd(nas))
R = daily(reversal_usd("NAS100"))
lo = max(Z.index.min(), O.index.min()); hi = min(Z.index.max(), O.index.max(), R.index.max())
cal = pd.date_range(lo, hi, freq="B", tz="UTC")
base = sum(s.reindex(cal).fillna(0) for s in (Z, O, R))      # daily $ at BASE size (0.01-0.02 lot)
YEARS = (cal[-1] - cal[0]).days / 365.25


def simulate(base, cal, eq0=EQ0, step=1000.0, gov_pct=None, gov_release=None):
    """step=None -> fixed base size (no compounding). gov_pct: halt rest of month once equity is
    gov_pct below its all-time peak; only active while eq < gov_release (None = always active)."""
    b = base.values.astype(float); months = cal.to_period("M")
    eq = eq0; peak = eq0; cur = None; halted = False
    curve = np.empty(len(cal)); ks = np.zeros(len(cal))
    for i in range(len(cal)):
        if months[i] != cur: cur = months[i]; halted = False
        gov_on = gov_pct is not None and (gov_release is None or eq < gov_release)
        if halted and gov_on:
            curve[i] = eq; continue
        k = 1 if step is None else max(1, int(eq // step))
        eq += b[i] * k
        peak = max(peak, eq)
        if gov_on and (peak - eq) >= gov_pct * peak: halted = True
        ks[i] = k; curve[i] = eq
    return pd.Series(curve, index=cal), pd.Series(ks, index=cal)


def stats(curve, ks):
    dd = (curve - curve.cummax()) / curve.cummax()
    ret = curve.pct_change().fillna(0)
    greens = 0; tot = 0; wyr = 0.0
    for y in sorted(set(cal.year)):
        m = cal.year == y; c = curve[m]
        if len(c) < 5: continue
        tot += 1
        if c.iloc[-1] > c.iloc[0]: greens += 1
        d = ((c - c.cummax()) / c.cummax()).min(); wyr = min(wyr, float(d))
    cagr = (curve.iloc[-1] / EQ0) ** (1 / YEARS) - 1
    sh = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else np.nan
    return dict(term=curve.iloc[-1], cagr=cagr, mdd=float(dd.min()), wyr=wyr,
                greens=greens, tot=tot, sh=sh, kmax=int(ks.max()), kend=int(ks.iloc[-1]))


def line(lbl, curve, ks):
    s = stats(curve, ks)
    print(f"{lbl:<34}{s['term']:>9,.0f}{s['cagr']:>8.0%}{s['mdd']:>8.0%}{s['wyr']:>8.0%}"
          f"  {s['greens']}/{s['tot']}{s['sh']:>7.2f}{s['kmax']:>6}")


print("=" * 92)
print(f"COMPOUNDING from ${EQ0:,.0f}  |  book: Z(ADX{ADX_GATE}) + ORB + Reversal  |  "
      f"{cal[0].date()} -> {cal[-1].date()} ({YEARS:.1f}y)")
print("=" * 92)
print(f"{'config':<34}{'final$':>9}{'CAGR':>8}{'maxDD':>8}{'worstYr':>8}  green{'Sharpe':>7}{'maxK':>6}")
print("-" * 92)

curve, ks = simulate(base, cal, step=None)
line("fixed 0.01 lot (no compounding)", curve, ks)
print("-" * 92)
for step in [500, 750, 1000, 1500, 2000, 2500, 3000, 4000]:
    c, k = simulate(base, cal, step=step)
    line(f"compound: 1 lot-step per ${step}", c, k)

print("-" * 92)
print("Early-stretch protection: trailing DD governor active ONLY below the release level,")
print("then compounding runs free (STEP=$3000, the steady-state 10-12% setting).")
print("-" * 92)
for rel in [3000, 5000]:
    for g in [0.12, 0.10, 0.08]:
        c, k = simulate(base, cal, step=3000, gov_pct=g, gov_release=rel)
        line(f"STEP3000 + gov {g:.0%} until ${rel}", c, k)

print("-" * 92)
print("Same protection, but on the aggressive STEP=$1000 setting:")
print("-" * 92)
for g in [0.12, 0.10, 0.08]:
    c, k = simulate(base, cal, step=1000, gov_pct=g, gov_release=3000)
    line(f"STEP1000 + gov {g:.0%} until $3000", c, k)

print("\nNOTE: 2021-2026 is ONE in-sample path and 2024-26 is a historic gold bull. Terminal $ is")
print("leverage x edge x that regime -- treat CAGR as a ceiling, not an expectation. maxDD is")
print("REALIZED; the stopless Reversal sleeve can float worse intraday before it closes.")
print("DONE")
