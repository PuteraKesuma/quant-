"""WMT RACE v2 — now with the REAL rules: static max-loss floor (6% or 10% of INITIAL,
equity-based) AND a separate DAILY loss barrier (reference = day-start equity minus a fixed
InitialBalance*daily% amount). Both are absorbing. Account $10k, equity now $9,600, target $11,000.

Daily-resolution Monte Carlo: block-bootstrap the book's DAILY closed PnL (5-day blocks to keep
losing streaks together), walk day by day, kill the path on the FIRST barrier touched.

HONEST CAVEAT built in: both WMT barriers are on intraday EQUITY incl. OPEN positions. This sim
uses CLOSED daily PnL, so it UNDER-counts intraday drawdown (esp. Z, always-in, wide ATR stop).
=> the daily-rule survival here is an OPTIMISTIC upper bound. Treat P(daily breach) as a floor.
Run: python research/wmt_race_daily.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")

START, TARGET, INIT = 9600.0, 11000.0, 10000.0
N_PATHS, MAX_D, BLOCK = 6000, 600, 5     # ~2.5yr, weekly blocks

print("=== building component series (same sims as portfolio_best) ===", flush=True)
import portfolio_best as pb   # noqa: E402
BOOK = pd.concat([pb.Z, pb.NAS, pb.LIQ]).sort_index()
# daily CLOSED PnL (calendar days with >=1 trade); zero-fill trading gaps handled by bootstrap
daily = BOOK.resample("D").sum()
daily = daily[daily.index.dayofweek < 5]          # weekdays only
D = daily.values
print(f"book daily PnL: n={len(D)} mean=${D.mean():+.2f} sd=${D.std():.2f} "
      f"min=${D.min():+.0f} p5=${np.percentile(D,5):+.0f}")

rng = np.random.default_rng(7)
nblk = int(np.ceil(MAX_D / BLOCK))


def race(mult, floor, daily_lim):
    """Return P(target), P(total-breach), P(daily-breach), P(unresolved), median days->target."""
    starts = rng.integers(0, len(D), size=(N_PATHS, nblk))
    idx = (starts[:, :, None] + np.arange(BLOCK)[None, None, :]) % len(D)
    draws = D[idx].reshape(N_PATHS, -1)[:, :MAX_D] * mult
    eq = START
    equity = np.full(N_PATHS, START)
    status = np.zeros(N_PATHS, int)        # 0 running, 1 target, 2 total-breach, 3 daily-breach
    day_hit = np.full(N_PATHS, MAX_D + 1)
    for d in range(MAX_D):
        run = status == 0
        if not run.any():
            break
        ref = equity.copy()                 # day-start reference (approx prev equity/balance)
        pnl = draws[:, d]
        equity = np.where(run, equity + pnl, equity)
        # daily breach: day's loss exceeds the fixed daily limit
        db = run & (equity < ref - daily_lim)
        # total breach: equity below the static floor
        tb = run & ~db & (equity <= floor)
        tg = run & ~db & ~tb & (equity >= TARGET)
        status = np.where(db, 3, np.where(tb, 2, np.where(tg, 1, status)))
        day_hit = np.where(tg & (day_hit > MAX_D), d + 1, day_hit)
    p_t = (status == 1).mean(); p_tb = (status == 2).mean()
    p_db = (status == 3).mean(); p_un = (status == 0).mean()
    med = float(np.median(day_hit[status == 1])) if (status == 1).any() else float("nan")
    return p_t, p_tb, p_db, p_un, med


print("\n=== FULL book (Z+NAS+LIQ). Race $9,600 -> $11,000, kill on first barrier ===")
print("floor = static max-loss; daily = fixed daily-loss cap (both from $10k initial)\n")
for floor, ftag in ((9000.0, "10% floor $9000 (buffer $600)"), (9400.0, "6% floor $9400 (buffer $200)")):
    for daily_lim, dtag in ((500.0, "daily 5% $500"), (400.0, "daily 4% $400"), (10000.0, "daily OFF")):
        print(f"-- {ftag} | {dtag} --")
        print(f"   {'lot':>5} {'P(target)':>10} {'P(totalDD)':>11} {'P(dailyDD)':>11} {'P(>2.5y)':>9} {'med days':>9}")
        for mult, lot in ((1, "0.01"), (2, "0.02"), (3, "0.03")):
            p_t, p_tb, p_db, p_un, med = race(mult, floor, daily_lim)
            md = f"{med:.0f} (~{med/21:.0f}mo)" if med == med else "n/a"
            print(f"   {lot:>5} {100*p_t:>9.1f}% {100*p_tb:>10.1f}% {100*p_db:>10.1f}% {100*p_un:>8.1f}% {md:>12}")
        print()
print("CAVEAT: daily-breach here is OPTIMISTIC (closed PnL; real intraday equity dips more).")
print("DONE")
