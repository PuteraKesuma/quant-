"""BOOK VALIDATION @ $500 — the deployed book (Z + NAS-ORB + LIQ), tested end-to-end:

  (1) FULL BACKTEST 2021-2026 per component + combined book ($ at 0.01 lot, the deployed size:
      at $500 the capital-aware lot cap keeps Z at lot_min 0.01, so 0.01-lot $ is EXACT).
      NOTE: the ORB series fills AT the range boundary — which is what live NOW does too
      (orb_stop_manager pending STOP, 2026-07-04). Before that, live paid ~8.6 pt/trade more.
  (2) WALK-FORWARD: fixed params (nothing re-fit), so WF = sequential out-of-sample windows;
      every 2-month segment is scored PF/net — consistency over time, worst window, % green.
  (3) MONTE CARLO from $500: weekly-PnL bootstrap (iid AND 4-week blocks to respect regime
      clustering), 4000 paths, horizons 13/26/52 weeks. Reports median/5th/95th equity,
      maxDD distribution, P(hit the -30% hard stop = $350), P(finish below start).
  (4) FORWARD CONE numbers = the honest 'prediction' (returns are NOT forecastable — ridge
      OOS R2 was negative — so the cone from the trade distribution IS the prediction).

Run: python research/book_validation_500.py   (heavy: rebuilds all three sims from duckdb M1)
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
from audit_live_strategies import stats, split

START = 500.0                 # user's planned capital
HARD_STOP = 0.30              # locked go/no-go criterion -> equity floor
FLOOR = START * (1 - HARD_STOP)
N_PATHS = 4000
OUT_PNG = (r"C:\Users\ADMINI~1\AppData\Local\Temp\1\claude\C--Users-Administrator"
           r"\91e0ccf1-c993-48f2-8268-f1678ad108cb\scratchpad\book_validation_500.png")

print("=== building component series (same sims as portfolio_best) ===", flush=True)
import portfolio_best as pb   # noqa: E402  (runs the audited Z/NAS/LIQ sims on import)
Z, NAS, LIQ = pb.Z, pb.NAS, pb.LIQ
BOOK = pd.concat([Z, NAS, LIQ]).sort_index()

# ---------------------------------------------------------------- (1) backtest
def line(name, s):
    st = stats(list(s.values))
    items = list(zip(s.index, s.values)); i_, o = split(items)
    eq = s.cumsum(); dd = float((eq - eq.cummax()).min())
    wr = 100.0 * (s > 0).mean()
    exp = float(s.mean())
    print(f"  {name:14s} n={st['n']:4d} PF={st['pf']:4.2f} IS={stats(i_)['pf']:4.2f} "
          f"OOS={stats(o)['pf']:4.2f} WR={wr:4.1f}% exp=${exp:+5.2f} "
          f"net=${s.sum():+6.0f} maxDD=${dd:+6.0f}")

print("\n=== (1) FULL BACKTEST 2021-2026 ($ @0.01 lot) ===")
line("Z (zrev 1H)", Z); line("NAS (ORB)", NAS); line("LIQ (15m)", LIQ)
line("BOOK (all 3)", BOOK)

print("\n  per-year net $ (book + components):")
years = sorted(set(BOOK.index.year))
print(f"    {'year':6s} {'Z':>7s} {'NAS':>7s} {'LIQ':>7s} {'BOOK':>8s}")
for y in years:
    zy = float(Z[Z.index.year == y].sum()); ny = float(NAS[NAS.index.year == y].sum())
    ly = float(LIQ[LIQ.index.year == y].sum()); by = zy + ny + ly
    print(f"    {y:<6d} {zy:>+7.0f} {ny:>+7.0f} {ly:>+7.0f} {by:>+8.0f}")

# ---------------------------------------------------------------- (2) walk-forward
print("\n=== (2) WALK-FORWARD: sequential 2-month OOS windows (fixed params, no re-fit) ===")
win = BOOK.groupby(pd.Grouper(freq="2MS"))
rows = []
for ts, seg in win:
    if len(seg) == 0:
        continue
    g = float(seg[seg > 0].sum()); l = float(-seg[seg < 0].sum())
    pf = g / l if l > 0 else float("inf")
    rows.append((ts, len(seg), pf, float(seg.sum())))
green = sum(1 for _, _, _, net in rows if net > 0)
pf1 = sum(1 for _, _, pf, _ in rows if pf >= 1.0)
worst = min(rows, key=lambda r: r[3]); best = max(rows, key=lambda r: r[3])
print(f"  windows={len(rows)}  green(net>0)={green}/{len(rows)} ({100*green/len(rows):.0f}%)  "
      f"PF>=1: {pf1}/{len(rows)}")
print(f"  worst window: {worst[0]:%Y-%m} net=${worst[3]:+.0f} (n={worst[1]})")
print(f"  best  window: {best[0]:%Y-%m} net=${best[3]:+.0f} (n={best[1]})")
print("  last 8 windows:")
for ts, n, pf, net in rows[-8:]:
    print(f"    {ts:%Y-%m}  n={n:3d}  PF={min(pf, 99):5.2f}  net=${net:+7.0f}")

# ---------------------------------------------------------------- (3) monte carlo @ $500
wk = BOOK.resample("W").sum()            # weekly $ PnL, zero weeks included
mu, sd = float(wk.mean()), float(wk.std())
posw = 100.0 * (wk > 0).mean()
print(f"\n=== (3) MONTE CARLO from ${START:.0f} (weekly bootstrap, {N_PATHS} paths) ===")
print(f"  weekly dist: n={len(wk)} mean=${mu:+.1f} sd=${sd:.1f} positive-weeks={posw:.0f}%")

rng = np.random.default_rng(11)
W = wk.values


def simulate(horizon, block):
    """Bootstrap paths of weekly PnL. block=1 -> iid; block>1 -> circular block bootstrap
    (keeps losing streaks together — honest about regime clustering)."""
    if block == 1:
        draws = rng.choice(W, size=(N_PATHS, horizon), replace=True)
    else:
        nblk = int(np.ceil(horizon / block))
        starts = rng.integers(0, len(W), size=(N_PATHS, nblk))
        idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % len(W)
        draws = W[idx].reshape(N_PATHS, -1)[:, :horizon]
    eq = START + np.cumsum(draws, axis=1)
    finals = eq[:, -1]
    peaks = np.maximum.accumulate(np.maximum(eq, START), axis=1)
    dd = (eq - peaks).min(axis=1)
    hit_floor = (eq.min(axis=1) <= FLOOR)
    return finals, dd, hit_floor


for label, block in (("iid", 1), ("4-week blocks", 4)):
    print(f"\n  -- {label} bootstrap --")
    for hor, hname in ((13, "13w (~3 bulan)"), (26, "26w (~6 bulan)"), (52, "52w (~1 tahun)")):
        finals, dd, hit = simulate(hor, block)
        p5, p50, p95 = np.percentile(finals, [5, 50, 95])
        print(f"  {hname:16s} median=${p50:6.0f}  5%=${p5:6.0f}  95%=${p95:6.0f}  "
              f"P(sentuh ${FLOOR:.0f})={100*hit.mean():4.1f}%  P(akhir<${START:.0f})={100*(finals < START).mean():4.1f}%  "
              f"maxDD med=${np.percentile(dd, 50):+5.0f} p95=${np.percentile(dd, 5):+5.0f}")

# ---------------------------------------------------------------- (4) chart
# cone over 52w = per-week percentiles across block-bootstrap paths
nblk = int(np.ceil(52 / 4))
starts = rng.integers(0, len(W), size=(N_PATHS, nblk))
idx = (starts[:, :, None] + np.arange(4)[None, None, :]) % len(W)
draws = W[idx].reshape(N_PATHS, -1)[:, :52]
eqp = START + np.cumsum(draws, axis=1)
p5c, p50c, p95c = np.percentile(eqp, [5, 50, 95], axis=0)
wks = np.arange(1, 53)

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13.5, 5.2))
eq_hist = BOOK.cumsum()
ax1.plot(eq_hist.index, START + eq_hist.values - eq_hist.values[0], lw=0.9, color="#1f77b4")
ax1.set_title("Backtest equity 2021-2026 (book, dari $500, 0.01 lot)")
ax1.grid(alpha=0.3); ax1.set_ylabel("$")
ax2.fill_between(wks, p5c, p95c, alpha=0.15, color="#1f77b4", label="cone 5-95%")
ax2.plot(wks, p50c, color="#1f77b4", lw=1.4, label=f"median (52w: ${p50c[-1]:.0f})")
ax2.plot(wks, p5c, color="#c0392b", ls=":", lw=1.0, label=f"floor 5% (${p5c[-1]:.0f})")
ax2.axhline(FLOOR, color="#c0392b", ls="--", lw=0.9, label=f"hard stop -30% (${FLOOR:.0f})")
ax2.axhline(START, color="gray", ls=":", lw=0.7)
ax2.set_title("Proyeksi 52 minggu dari $500 (block bootstrap)")
ax2.set_xlabel("minggu"); ax2.grid(alpha=0.3); ax2.legend(fontsize=8, loc="upper left")
plt.tight_layout(); plt.savefig(OUT_PNG, dpi=110)
print(f"\nchart: {OUT_PNG}")
print("\nDONE")
