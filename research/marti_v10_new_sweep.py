"""Semi Marti Cuan v10 (NEW download, 2026-08-18) - basket TP/SL sweep + per-year test.

WHAT THIS ANSWERS
  User: "backtest M5 2026, lot cap 0.02, cari SL yang enak di berapa dan TP di berapa".
  The EA's real exit knobs are InpGlobalTP_USD / InpGlobalSL_USD (basket $ P&L), so the
  sweep is over those, not over per-position pips.

SIGNAL
  Reuses research/marti_signal_port.py's spec, which was written for exactly this
  version's params (InpSignalMode=2, SMA21, norm 100, levels 80/20, hours 9-23 server,
  MACD 5/13/9) and re-validated today at 4% vs the MT5 tester's 49 series on M15/2026.
  The hour filter FREEZES the confirmation state machine (EA line 1364 early-return) -
  copied exactly; getting this wrong loses ~14 signals.

BASKET MODEL (read line-by-line from the .mq5, not guessed)
  digits=2 on FBS XAUUSD -> line 1100: pipMul = (digits>3)?10:1 = 1.0
                            line 1101: baseGap = 25 * 0.01 * 1.0 = $0.25
  Dual entry on the signal bar: 2 positions at InpStartLot (0.01 each).
  Three limit layers at 1.5/2.5/4.0 x baseGap adverse = $0.375 / $0.625 / $1.00,
  lots x1.5 each and CAPPED at the user's 0.02: 0.015, 0.02, 0.02.
  Limit expiry 240 min (InpLimitExpiryMins) - a layer that is never touched expires.
  => a fully-filled basket is 0.075 lots, and because the deepest layer sits only $1
     from entry, gold normally fills the whole ladder within a bar or two.
  Exit: basket P&L >= TP_USD (win) or <= -SL_USD (loss, when SL>0). SL=0 = the EA's
  shipped setting = no basket stop.
  Trailing (InpUseTrailingUSD, start $10 step $2) is modelled on the basket: once peak
  P&L >= start, a floor rides at peak-step and closes the basket when breached.

NOT MODELLED (stated so the numbers are read correctly)
  - MT5 news filter (InpUseNewsFilter=true) - no calendar in this harness. It only ever
    BLOCKS entries, so real trade count <= what is shown here.
  - Per-position TP $10 on dual-entry #1; the basket TP/trailing dominates the exit and
    modelling it separately would not change which SL/TP pair wins.
  - Spread/commission: charged as COST_USD per basket (round turn, all legs).
  Both simplifications are optimistic, so a config that loses here loses in reality too.

Run: python research/marti_v10_new_sweep.py
"""
from __future__ import annotations

import warnings

warnings.filterwarnings("ignore")

import datetime as dt

import MetaTrader5 as mt5
import numpy as np
import pandas as pd

SRV_OFFSET_H = 3
LVL_HI, LVL_LO = 80.0, 20.0
SMA_PERIOD, NORM_PERIOD = 21, 100
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 5, 13, 9
HOUR_START, HOUR_END = 9, 23

CONTRACT = 100.0
START_LOT, LOT_MULT, LOT_CAP = 0.01, 1.5, 0.02      # user: never above 0.02
BASE_GAP = 25 * 0.01 * 1.0                           # $0.25 (digits=2 -> pipMul 1)
DEPTHS = [1.5, 2.5, 4.0]
LIMIT_EXPIRY_MIN = 240
TRAIL_START, TRAIL_STEP = 10.0, 2.0
COST_USD = 0.60                                      # spread+comm per basket, all legs
MAX_HOLD_BARS = {"M5": 8640, "M15": 2880}            # 30 days


def fetch(tf, tf_name, stop_year):
    frames, anchor = [], dt.datetime(2026, 8, 19)
    for _ in range(80):
        r = mt5.copy_rates_from("XAUUSD", tf, anchor, 20000)
        if r is None or len(r) == 0:
            break
        d = pd.DataFrame(r)
        d["ts"] = pd.to_datetime(d["time"], unit="s", utc=True)
        frames.append(d)
        first = d["ts"].min()
        if first.year < stop_year:
            break
        anchor = first.to_pydatetime().replace(tzinfo=None)
    a = pd.concat(frames).drop_duplicates("ts").sort_values("ts")
    return a.set_index("ts")[["open", "high", "low", "close"]]


def build_signals(df):
    """Confirmed BUY/SELL series - identical logic to marti_signal_port.build_signals."""
    c = df["close"].to_numpy()
    n = len(c)
    sma = pd.Series(c).rolling(SMA_PERIOD).mean().to_numpy()
    s = pd.Series(sma)
    mn, mx = s.rolling(NORM_PERIOD).min().to_numpy(), s.rolling(NORM_PERIOD).max().to_numpy()
    rng = mx - mn
    sma_norm = np.clip(np.where(rng != 0, (sma - mn) / np.where(rng == 0, 1, rng) * 100.0, 50.0), 0, 100)

    ef = pd.Series(c).ewm(span=MACD_FAST, adjust=False).mean().to_numpy()
    es = pd.Series(c).ewm(span=MACD_SLOW, adjust=False).mean().to_numpy()
    macd_sig = pd.Series(ef - es).rolling(MACD_SIGNAL).mean().to_numpy()
    ms = pd.Series(macd_sig)
    mn2, mx2 = ms.rolling(NORM_PERIOD).min().to_numpy(), ms.rolling(NORM_PERIOD).max().to_numpy()
    rng2 = mx2 - mn2
    macd_norm = np.clip(np.where(rng2 != 0, (macd_sig - mn2) / np.where(rng2 == 0, 1, rng2) * 100.0, 50.0), 0, 100)

    srv_hour = (df.index + pd.Timedelta(hours=SRV_OFFSET_H)).hour.to_numpy()
    sig = np.zeros(n, dtype=int)
    wait_s = wait_b = pull_s = pull_b = False
    for i in range(n):
        if np.isnan(sma_norm[i]) or np.isnan(macd_norm[i]):
            continue
        if not (HOUR_START <= srv_hour[i] <= HOUR_END):
            continue                                   # freezes the state machine
        raw_sell, raw_buy = sma_norm[i] >= LVL_HI, sma_norm[i] <= LVL_LO
        if raw_sell:
            if not wait_s:
                wait_s, pull_s = True, False
            elif pull_s:
                sig[i] = -1
                wait_s = pull_s = False
        elif wait_s and not pull_s and macd_norm[i] < LVL_HI and sma_norm[i] < LVL_HI:
            pull_s = True
        if raw_buy:
            if not wait_b:
                wait_b, pull_b = True, False
            elif pull_b:
                if sig[i] == 0:
                    sig[i] = 1
                wait_b = pull_b = False
        elif wait_b and not pull_b and macd_norm[i] > LVL_LO and sma_norm[i] > LVL_LO:
            pull_b = True
        if raw_sell and wait_b:
            wait_b = pull_b = False
        if raw_buy and wait_s:
            wait_s = pull_s = False
    return sig


def ladder():
    """(depth_in_price, lot) per leg, capped at LOT_CAP. Legs 0/1 are the dual entry."""
    legs = [(0.0, START_LOT), (0.0, START_LOT)]
    lot = START_LOT
    for d in DEPTHS:
        lot = min(lot * LOT_MULT, LOT_CAP)
        legs.append((d * BASE_GAP, lot))
    return legs


LEGS = ladder()


def run_basket(hi, lo, cl, i, side, tp_usd, sl_usd, max_hold, bar_min):
    """Simulate one basket forward from bar i. Returns (pnl_usd, bars_held, why)."""
    entry = cl[i]
    a = 1 if side == 1 else -1
    filled = [False] * len(LEGS)
    tot_lot = 0.0
    cost_basis = 0.0                                   # sum(lot * fill_price)
    for k, (depth, lot) in enumerate(LEGS):
        if depth == 0.0:
            filled[k] = True
            tot_lot += lot
            cost_basis += lot * entry
    peak = -1e18
    trail_floor = None
    expiry_bars = int(LIMIT_EXPIRY_MIN / bar_min)
    end = min(len(cl), i + 1 + max_hold)
    for j in range(i + 1, end):
        # fill pending limit layers touched by this bar (adverse direction)
        if j - i <= expiry_bars:
            for k, (depth, lot) in enumerate(LEGS):
                if filled[k] or depth == 0.0:
                    continue
                lvl = entry - a * depth
                touched = (lo[j] <= lvl) if a == 1 else (hi[j] >= lvl)
                if touched:
                    filled[k] = True
                    tot_lot += lot
                    cost_basis += lot * lvl
        avg = cost_basis / tot_lot
        # intrabar extremes of basket P&L
        best_px = hi[j] if a == 1 else lo[j]
        worst_px = lo[j] if a == 1 else hi[j]
        pnl_best = (best_px - avg) * a * tot_lot * CONTRACT
        pnl_worst = (worst_px - avg) * a * tot_lot * CONTRACT
        # loss checked first (conservative: adverse excursion wins ties)
        if sl_usd > 0 and pnl_worst <= -sl_usd:
            return -sl_usd - COST_USD, j - i, "SL"
        if trail_floor is not None and pnl_worst <= trail_floor:
            return trail_floor - COST_USD, j - i, "TRAIL"
        if pnl_best >= tp_usd:
            return tp_usd - COST_USD, j - i, "TP"
        if pnl_best > peak:
            peak = pnl_best
            if peak >= TRAIL_START:
                trail_floor = max(trail_floor or -1e18, peak - TRAIL_STEP)
    avg = cost_basis / tot_lot
    return (cl[end - 1] - avg) * a * tot_lot * CONTRACT - COST_USD, end - 1 - i, "OPEN"


def backtest(df, sig, tp_usd, sl_usd, tf_name, bar_min, capital=1000.0):
    hi, lo, cl = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    idx = df.index
    max_hold = MAX_HOLD_BARS[tf_name]
    rows, eq, peak_eq, maxdd, busted = [], capital, capital, 0.0, False
    j_free = -1
    for i in np.nonzero(sig)[0]:
        if i <= j_free:
            continue                                    # basket already open
        pnl, held, why = run_basket(hi, lo, cl, i, sig[i], tp_usd, sl_usd, max_hold, bar_min)
        j_free = i + held
        eq += pnl
        peak_eq = max(peak_eq, eq)
        maxdd = max(maxdd, (peak_eq - eq) / peak_eq * 100.0)
        rows.append(dict(ts=idx[i], side=sig[i], pnl=pnl, held=held, why=why, eq=eq))
        if eq <= 0:
            busted = True
            break
    return pd.DataFrame(rows), eq, maxdd, busted


def summarise(tr, eq, maxdd, busted, capital=1000.0):
    if not len(tr):
        return dict(n=0)
    w, l = tr[tr.pnl > 0], tr[tr.pnl <= 0]
    gp, gl = w.pnl.sum(), -l.pnl.sum()
    return dict(n=len(tr), net=eq - capital, pf=(gp / gl if gl > 0 else np.inf),
                wr=len(w) / len(tr) * 100, avgW=(w.pnl.mean() if len(w) else 0),
                avgL=(l.pnl.mean() if len(l) else 0), dd=maxdd, bust=busted)


if __name__ == "__main__":
    assert mt5.initialize(), mt5.last_error()
    print(f"ladder legs (depth $, lot): {[(round(d,3), l) for d, l in LEGS]}"
          f"  total {sum(l for _, l in LEGS):.3f} lot")

    m5 = fetch(mt5.TIMEFRAME_M5, "M5", 2025)
    print(f"\nM5 {len(m5)} bars {m5.index.min():%Y-%m-%d} -> {m5.index.max():%Y-%m-%d}")
    s5 = build_signals(m5)
    print(f"M5 signals: {int((s5 != 0).sum())}")

    y26 = (m5.index >= "2026-01-01")
    df26, sg26 = m5[y26], s5[y26]

    print("\n" + "=" * 96)
    print("SWEEP on M5 2026 (lot cap 0.02, capital $1000)")
    print("=" * 96)
    print(f"{'TP$':>5} {'SL$':>6} {'n':>4} {'net$':>9} {'PF':>6} {'WR%':>6} "
          f"{'avgW':>7} {'avgL':>8} {'maxDD%':>7} {'bust':>5}")
    best, results = None, []
    for tp in [10, 15, 25, 40, 60]:
        for sl in [0, 25, 50, 75, 100, 150, 200]:
            tr, eq, dd, bust = backtest(df26, sg26, tp, sl, "M5", 5)
            r = summarise(tr, eq, dd, bust)
            if not r.get("n"):
                continue
            results.append((tp, sl, r))
            print(f"{tp:>5} {sl:>6} {r['n']:>4} {r['net']:>9.2f} {r['pf']:>6.2f} "
                  f"{r['wr']:>6.1f} {r['avgW']:>7.2f} {r['avgL']:>8.2f} {r['dd']:>7.2f} "
                  f"{'YES' if r['bust'] else '-':>5}")
            if not r["bust"] and (best is None or r["net"] > best[2]["net"]):
                best = (tp, sl, r)

    if best:
        tp, sl, r = best
        req_wr = -r["avgL"] / (r["avgW"] - r["avgL"]) * 100
        print(f"\nBEST 2026 (M5): TP ${tp} SL ${sl} -> net ${r['net']:.2f} PF {r['pf']:.2f} "
              f"WR {r['wr']:.1f}% maxDD {r['dd']:.2f}%")
        print(f"  breakeven WR = {req_wr:.1f}%  (achieved {r['wr']:.1f}%)")

        print("\n" + "=" * 96)
        print(f"OUT-OF-SAMPLE: same TP ${tp} / SL ${sl}, per year")
        print("=" * 96)
        print(f"{'TF':>4} {'year':>6} {'n':>5} {'net$':>10} {'PF':>6} {'WR%':>6} "
              f"{'reqWR%':>7} {'maxDD%':>8} {'bust':>5}")
        # M5: 2025 (from Mar) and 2026
        for yr in [2025, 2026]:
            m = (m5.index.year == yr)
            if m.sum() < 500:
                continue
            tr, eq, dd, bust = backtest(m5[m], s5[m], tp, sl, "M5", 5)
            rr = summarise(tr, eq, dd, bust)
            if not rr.get("n"):
                continue
            rw = -rr["avgL"] / (rr["avgW"] - rr["avgL"]) * 100 if rr["avgW"] != rr["avgL"] else np.nan
            print(f"{'M5':>4} {yr:>6} {rr['n']:>5} {rr['net']:>10.2f} {rr['pf']:>6.2f} "
                  f"{rr['wr']:>6.1f} {rw:>7.1f} {rr['dd']:>8.2f} {'YES' if rr['bust'] else '-':>5}")
        # M15 for the long horizon
        m15 = fetch(mt5.TIMEFRAME_M15, "M15", 2022)
        s15 = build_signals(m15)
        print(f"\n(M15 {len(m15)} bars {m15.index.min():%Y-%m-%d} -> {m15.index.max():%Y-%m-%d}, "
              f"{int((s15!=0).sum())} signals)")
        for yr in sorted(set(m15.index.year)):
            m = (m15.index.year == yr)
            if m.sum() < 500:
                continue
            tr, eq, dd, bust = backtest(m15[m], s15[m], tp, sl, "M15", 15)
            rr = summarise(tr, eq, dd, bust)
            if not rr.get("n"):
                continue
            rw = -rr["avgL"] / (rr["avgW"] - rr["avgL"]) * 100 if rr["avgW"] != rr["avgL"] else np.nan
            print(f"{'M15':>4} {yr:>6} {rr['n']:>5} {rr['net']:>10.2f} {rr['pf']:>6.2f} "
                  f"{rr['wr']:>6.1f} {rw:>7.1f} {rr['dd']:>8.2f} {'YES' if rr['bust'] else '-':>5}")
        # continuous run across the whole M15 history at the same setting
        tr, eq, dd, bust = backtest(m15, s15, tp, sl, "M15", 15)
        rr = summarise(tr, eq, dd, bust)
        print(f"\nCONTINUOUS M15 2022-05..2026-08 at TP ${tp}/SL ${sl}: "
              f"n={rr['n']} net=${rr['net']:.2f} PF={rr['pf']:.2f} WR={rr['wr']:.1f}% "
              f"maxDD={rr['dd']:.2f}% bust={'YES' if rr['bust'] else 'no'}")
    mt5.shutdown()
