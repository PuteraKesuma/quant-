"""WMT 1-MONTH CHALLENGE — the honest odds of $9,600 -> $11,000 in ~21 trading days.

Two models, both with WMT barriers (floor $9,000 static, daily $500), absorbing on first hit:
  PART A: the VALIDATED book (Z+NAS+LIQ) daily PnL, block-bootstrapped, scaled by a lot
          multiplier (= risk-per-trade proxy). Shows P(reach $11k in 21d) vs P(blow) per size.
  PART B: the user's idealized SWING (SL $80, TP $250 = ~3:1) at assumed win-rates, ~1.5
          trades/day (~32 trades/month). Shows the win-rate DEPENDENCY + ruin, since a made-up
          far-TP strategy's outcome is entirely its (unvalidated) hit-rate.

Purpose: answer 'can we do +14.6% in a month' with probabilities, not opinion.
Run: python research/wmt_one_month.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd

sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")

START, TARGET, FLOOR, DAILY = 9600.0, 11000.0, 9000.0, 500.0
NEED = TARGET - START
HORIZON_D = 21          # ~1 trading month
N = 20000

print("=== building validated book (portfolio_best) ===", flush=True)
import portfolio_best as pb   # noqa: E402
BOOK = pd.concat([pb.Z, pb.NAS, pb.LIQ]).sort_index()
daily = BOOK.resample("D").sum()
daily = daily[daily.index.dayofweek < 5]
D = daily.values
rng = np.random.default_rng(3)
BLOCK = 5
nblk = int(np.ceil(HORIZON_D / BLOCK))
print(f"need +${NEED:.0f} in {HORIZON_D}d. book daily @0.01: mean=${D.mean():+.2f} "
      f"-> ~${D.mean()*HORIZON_D:+.0f}/month. To net +${NEED:.0f} needs ~{NEED/(D.mean()*HORIZON_D):.0f}x that.\n")


def book_month(mult):
    starts = rng.integers(0, len(D), size=(N, nblk))
    idx = (starts[:, :, None] + np.arange(BLOCK)[None, None, :]) % len(D)
    draws = D[idx].reshape(N, -1)[:, :HORIZON_D] * mult
    eq = np.full(N, START); status = np.zeros(N, int)
    for d in range(HORIZON_D):
        run = status == 0
        ref = eq.copy()
        eq = np.where(run, eq + draws[:, d], eq)
        db = run & (eq < ref - DAILY)
        tb = run & ~db & (eq <= FLOOR)
        tg = run & ~db & ~tb & (eq >= TARGET)
        status = np.where(db | tb, 2, np.where(tg, 1, status))
    return (status == 1).mean(), (status == 2).mean(), (status == 0).mean(), eq

print("=== PART A: VALIDATED book, 1 month, per lot-multiplier (risk/trade proxy) ===")
print(f"  {'mult':>5} {'~risk/trade':>11} {'P(+$1400 in 1mo)':>17} {'P(BLOW acct)':>13} {'P(neither)':>11} {'median end':>11}")
for mult in (1, 2, 3, 5, 8):
    pt, pb_, pn, eq = book_month(mult)
    rpt = f"~${25*mult:.0f}"   # book avg risk ~ $25/trade @0.01
    print(f"  {mult:>5} {rpt:>11} {100*pt:>16.1f}% {100*pb_:>12.1f}% {100*pn:>10.1f}% ${np.median(eq):>9.0f}")

print("\n=== PART B: idealized SWING SL$80 / TP$250 (~3.1:1), ~1.5 trades/day, 32/month ===")
SL, TP, TPD = 80.0, 250.0, 6   # 6 trades max/day before daily $500 cap (6*80=480)
NTR = 32
print(f"  {'win-rate':>9} {'exp/trade':>10} {'P(+$1400)':>10} {'P(BLOW)':>9} {'P(neither)':>11}  note")
for wr in (0.25, 0.30, 0.35, 0.40, 0.45):
    wins = rng.random((N, NTR)) < wr
    pnl = np.where(wins, TP, -SL)
    eq = np.full(N, START); status = np.zeros(N, int); dayloss = np.zeros(N)
    for k in range(NTR):
        run = status == 0
        if k % TPD == 0:
            dayloss = np.zeros(N)                 # new day
        step = np.where(run, pnl[:, k], 0.0)
        eq = eq + step
        dayloss = dayloss + np.where(step < 0, -step, 0.0)
        tb = run & (eq <= FLOOR)
        dbk = run & ~tb & (dayloss >= DAILY)
        tg = run & ~tb & ~dbk & (eq >= TARGET)
        status = np.where(tb | dbk, 2, np.where(tg, 1, status))
    exp = TP * wr - SL * (1 - wr)
    note = "edge NEGATIF" if exp <= 0 else ("tipis" if exp < 20 else "kuat (tak realistis di 3:1)")
    print(f"  {100*wr:>7.0f}% {exp:>+9.0f} {100*(status==1).mean():>9.1f}% {100*(status==2).mean():>8.1f}% "
          f"{100*(status==0).mean():>10.1f}%  {note}")
print("\n  ref: Z (validated) WR~37% @ win/loss ~2.8:1. A 3:1-RR system with WR>40% is essentially")
print("       nonexistent; break-even here is WR~24%. The month-target needs high WR AND many")
print("       trades -> whichever way, variance drives P(BLOW) up.")
print("\nDONE")
