"""FORWARD MODEL for the live book (Z ADX28 + ORB + Reversal, Golden OFF).

A backtest is ONE path through history. This models the DISTRIBUTION of what next month / next year
can look like, via stationary block bootstrap (L=20 trading days, preserves regime clustering) on the
book's real daily $ P&L at base 0.01 lot:

  1. FULL book, all regimes 2021-2026   -> the base case
  2. FULL book, LEAN regime only (2021-2023 days: gold chop/bear, thin years) -> the honest stress:
     "what if the next 12 months look like the no-gold-trend years?"
  3. NO-Z book (ORB+Rev only)           -> what carries us if Z's gate keeps it flat all year

Each: P(red month), monthly percentiles at fixed 0.01 lot, and 12-month paths from $1000 with the
live lot-step rule (k = max(1, eq//$1500)) -> terminal equity percentiles, maxDD percentiles,
P(DD worse than -19%), P(red year). These numbers ARE the kill criteria for the forward test:
live results below the model's 5th percentile = edge decay signal, halt and investigate.

Also quantifies the stopless Reversal sleeve's FLOATING adverse excursion (close-based, so an
UNDERestimate) -- the risk realized-only backtests hide.
"""
import os, sys
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"
import numpy as np, pandas as pd
sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import load_m1
from regime_fix import accurate_z_usd, orb_usd, daily

EQ0, STEP = 1000.0, 1500.0
N_PATHS, HOR, L = 5000, 252, 20
rng = np.random.default_rng(7)
px = pd.read_parquet(r"C:\Quant\data\Level_2_Datamart\universe_daily.parquet")


def rsi(c, n=2):
    d = c.diff(); u = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + u / dn.replace(0, np.nan))


def reversal_trades(name, dpp=0.10):
    """Same trades as regime_fix.reversal_usd, plus floating MAE (close-based) and hold days."""
    c = px[name].dropna(); r2 = rsi(c, 2); s200 = c.rolling(200).mean(); s5 = c.rolling(5).mean()
    out = []; ip = False; ei = None
    for i in range(1, len(c)):
        if not ip and r2.iloc[i] < 10 and c.iloc[i] > s200.iloc[i]: ip = True; ei = i
        elif ip and c.iloc[i] > s5.iloc[i]:
            seg = c.iloc[ei:i + 1]
            mae = (seg.min() - c.iloc[ei]) * dpp          # worst floating $ (closes only -> underestimate)
            out.append((c.index[i], (c.iloc[i] - c.iloc[ei]) * dpp, mae, i - ei)); ip = False
    return out


xau = load_m1("XAUUSD"); nas = load_m1("NAS100")
Z = daily(accurate_z_usd(xau, adx_min=28))
O = daily(orb_usd(nas))
rtr = reversal_trades("NAS100")
R = daily([(t, p) for t, p, _, _ in rtr])
lo = max(Z.index.min(), O.index.min()); hi = min(Z.index.max(), O.index.max(), R.index.max())
cal = pd.date_range(lo, hi, freq="B", tz="UTC")
Zc, Oc, Rc = Z.reindex(cal).fillna(0), O.reindex(cal).fillna(0), R.reindex(cal).fillna(0)
full = Zc + Oc + Rc
noz = Oc + Rc

# ---- historical ground truth: calendar months ----
mo = full.groupby(cal.to_period("M")).sum()
mo = mo.iloc[1:-1]                                        # drop partial first/last months
print("=" * 96)
print("HISTORICAL calendar months (full book, 0.01 lot):")
print(f"  n={len(mo)} | RED {int((mo < 0).sum())}/{len(mo)} ({100 * (mo < 0).mean():.0f}%) | "
      f"median ${mo.median():+.0f} | worst ${mo.min():+.0f} ({mo.idxmin()}) | best ${mo.max():+.0f} ({mo.idxmax()})")


def mc_paths(b, n, horizon, blk):
    nb = len(b); nblocks = int(np.ceil(horizon / blk))
    starts = rng.integers(0, nb - blk, size=(n, nblocks))
    out = np.empty((n, nblocks * blk))
    for j in range(nblocks):
        out[:, j * blk:(j + 1) * blk] = b[starts[:, j][:, None] + np.arange(blk)[None, :]]
    return out[:, :horizon]


def run_paths(paths, eq0=EQ0, step=STEP):
    n, T = paths.shape
    eq = np.full(n, eq0); peak = np.full(n, eq0); maxdd = np.zeros(n)
    for t in range(T):
        k = np.maximum(1, np.floor(eq / step))
        eq = eq + paths[:, t] * k
        peak = np.maximum(peak, eq)
        maxdd = np.minimum(maxdd, (eq - peak) / peak)
    return eq, maxdd


def pct(a, q): return float(np.percentile(a, q))


def report(label, book_vals, blk=L):
    b = np.asarray(book_vals, dtype=float)
    msum = mc_paths(b, N_PATHS, 21, blk).sum(axis=1)
    term, mdd = run_paths(mc_paths(b, N_PATHS, HOR, blk))
    print(f"\n--- {label} (block={blk}d, {N_PATHS} paths) ---")
    print(f"  1 BULAN  @0.01 lot : P(merah) {100 * np.mean(msum < 0):.0f}% | "
          f"p5 ${pct(msum, 5):+.0f} | p25 ${pct(msum, 25):+.0f} | median ${pct(msum, 50):+.0f} | p95 ${pct(msum, 95):+.0f}")
    print(f"  12 BULAN dari $1000: terminal p5 ${pct(term, 5):,.0f} | median ${pct(term, 50):,.0f} | p95 ${pct(term, 95):,.0f}")
    print(f"                       maxDD median {100 * pct(mdd, 50):.0f}% | p5(terburuk) {100 * pct(mdd, 5):.0f}% | "
          f"P(DD<-19%) {100 * np.mean(mdd < -0.19):.0f}% | P(tahun merah) {100 * np.mean(term < EQ0):.0f}%")
    return msum, term, mdd


report("FULL BOOK, semua regime 2021-2026", full.values)
lean = full[cal.year <= 2023]
report("FULL BOOK, REGIME KURUS saja (hari-hari 2021-2023, emas tidak trending)", lean.values)
report("TANPA Z (ORB+Reversal saja) — kalau gate bikin Z diam setahun", noz.values)

# block-length sensitivity on the base case
b = full.values.astype(float)
for blk in (10, 40):
    _, mdd = run_paths(mc_paths(b, N_PATHS, HOR, blk))
    print(f"  [sensitivitas] block={blk}d: maxDD median {100 * pct(mdd, 50):.0f}% | p5 {100 * pct(mdd, 5):.0f}%")

# ---- Reversal floating risk (the hidden hole) ----
mae = np.array([m for _, _, m, _ in rtr]); hold = np.array([h for _, _, _, h in rtr])
print(f"\nREVERSAL floating MAE @0.01 lot (close-based -> UNDERestimate): n={len(mae)} | "
      f"median ${np.median(mae):+.0f} | p95 ${pct(mae, 5):+.0f} | terburuk ${mae.min():+.0f} | "
      f"hold median {np.median(hold):.0f}d max {hold.max():.0f}d")
nas_now = float(px['NAS100'].dropna().iloc[-1])
print(f"Disaster stop live 5% dari harga (~{nas_now:,.0f}) = ~${nas_now * 0.05 * 0.10:,.0f} realized kalau kena @0.01 lot.")
print("\nCatatan: bootstrap mengasumsikan masa depan = campuran hari-hari 2021-2026. Regime BARU yang tidak")
print("pernah terjadi (mis. emas crash multi-bulan + NAS crash bersamaan) TIDAK ada di distribusi ini.")
print("DONE")
