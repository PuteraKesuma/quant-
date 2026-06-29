"""Auction-theory strategies that are BACKTESTABLE on our data (no footprint/DOM,
which FBS CFD can't provide). Two ideas, same skeptic gauntlet as the other audits:

  1. Initial Balance (IB) breakout — the first hour of a session sets the auction's
     initial balance; trade a break beyond IB with a range-extension target.
  2. Value Area fade — build the PRIOR day's volume profile (POC / VAH / VAL) and
     fade the next day's tap of a value-area edge back toward POC (mean reversion).

No-lookahead: IB uses only the first-hour range then trades after; VA fade uses the
*completed prior day's* profile. M1 intrabar fill, SL-before-TP, costs included,
IS<2025-01-01<=OOS, bootstrap Monte-Carlo 5th-pct PF.

Run: python research/auction_strategies.py
"""
import sys
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from audit_live_strategies import stats, split, fmt, per_year, mc_pf_p5, CUT


def load_m1_vol(sym: str) -> pd.DataFrame:
    con = duckdb.connect(str(ROOT / "data" / "Level_0_Raw" / f"{sym}_1m.duckdb"), read_only=True)
    rows = con.execute("SELECT epoch(ts),open,high,low,close,volume FROM ohlcv ORDER BY ts").fetchall()
    con.close()
    a = np.asarray(rows, float)
    return pd.DataFrame({"open": a[:, 1], "high": a[:, 2], "low": a[:, 3],
                         "close": a[:, 4], "volume": a[:, 5]},
                        index=pd.to_datetime(a[:, 0], unit="s", utc=True))


def _nas_open_min(d):
    et = dt.datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo("America/New_York"))
    return 14 * 60 + 30 if et.dst() == dt.timedelta(0) else 13 * 60 + 30


def _report(tag, items, cost_note="", extra=""):
    if not items:
        print(f"  {tag:30s} n=0"); return
    i_, o = split(items)
    pnl = np.array([p for _, p in items]); eq = np.cumsum(pnl); mdd = (eq - np.maximum.accumulate(eq)).min()
    py = per_year(items); g = sum(1 for v in py.values() if v[0] >= 1.0)
    so = stats(o); pf = "inf" if so["pf"] == float("inf") else f"{so['pf']:.2f}"
    si = stats(i_); pfi = "inf" if si["pf"] == float("inf") else f"{si['pf']:.2f}"
    print(f"  {tag:30s} n={len(items):4d} ISpf={pfi:>4} OOSpf={pf:>4} OOSnet={so['net']:+6.1f}R "
          f"maxDD={mdd:6.1f}R MCp5={mc_pf_p5(o):.2f} grn{g}/{len(py)} {extra}")


# ---------------------------------------------------------------- 1. IB breakout
def ib_breakout(m1, open_min_fn, ib_min=60, tp_mult=2.0, end_min=None, cost_r=0.05):
    """First `ib_min` of the session = initial balance. After it, first break beyond
    IB high/low enters; risk = IB range (stop at opposite IB edge), target = tp_mult*IB.
    Returns [(entry_ts, pnl_R)]. dst-aware open via open_min_fn(date)."""
    H = m1["high"].values; L = m1["low"].values; C = m1["close"].values
    mod = m1.index.hour.values * 60 + m1.index.minute.values
    dord = m1.index.normalize().asi8
    uniq, starts = np.unique(dord, return_index=True); starts = list(starts) + [len(m1)]
    items = []; ndays = 0; ntr = 0
    for di in range(len(uniq)):
        a, b = starts[di], starts[di + 1]; ndays += 1
        day = m1.index[a].date(); om = open_min_fn(day)
        em = end_min if end_min is not None else om + 8 * 60
        idx = np.arange(a, b); md = mod[a:b]
        ibm = (md >= om) & (md < om + ib_min)
        if ibm.sum() < ib_min // 2:
            continue
        ii = idx[ibm]; ibh = H[ii].max(); ibl = L[ii].min(); rng = ibh - ibl
        if rng <= 0:
            continue
        pidx = idx[md >= om + ib_min]
        ei = d = ent = None
        for i in pidx:
            if H[i] > ibh: ei, d, ent = i, 1, ibh; break
            if L[i] < ibl: ei, d, ent = i, -1, ibl; break
        if ei is None:
            continue
        ntr += 1
        stop = ent - d * rng                 # opposite IB edge -> risk = 1 IB range = 1R
        tp = ent + d * tp_mult * rng
        pnl = None
        for j in range(ei, b):
            if mod[j] >= em: pnl = d * (C[j] - ent) / rng - cost_r; break
            if d == 1:
                if L[j] <= stop: pnl = -1 - cost_r; break
                if H[j] >= tp: pnl = tp_mult - cost_r; break
            else:
                if H[j] >= stop: pnl = -1 - cost_r; break
                if L[j] <= tp: pnl = tp_mult - cost_r; break
        if pnl is None:
            pnl = d * (C[b - 1] - ent) / rng - cost_r
        items.append((m1.index[ei], pnl))
    return items, ndays, ntr


# ---------------------------------------------------------- 2. Value Area fade
def day_profile(dm1, bin_size):
    lo = float(dm1["low"].min()); hi = float(dm1["high"].max())
    if hi <= lo:
        return None
    bins = np.arange(lo, hi + bin_size, bin_size)
    if len(bins) < 3:
        return None
    tp = ((dm1["high"] + dm1["low"] + dm1["close"]) / 3).values
    vol = dm1["volume"].values
    k = np.clip(((tp - lo) / bin_size).astype(int), 0, len(bins) - 2)
    hist = np.zeros(len(bins) - 1)
    np.add.at(hist, k, vol)
    if hist.sum() <= 0:
        return None
    poc_b = int(hist.argmax())
    total = hist.sum(); target = 0.7 * total; acc = hist[poc_b]; lo_b = hi_b = poc_b
    while acc < target and (lo_b > 0 or hi_b < len(hist) - 1):
        left = hist[lo_b - 1] if lo_b > 0 else -1.0
        right = hist[hi_b + 1] if hi_b < len(hist) - 1 else -1.0
        if right >= left: hi_b += 1; acc += hist[hi_b]
        else: lo_b -= 1; acc += hist[lo_b]
    poc = bins[poc_b] + bin_size / 2
    val = bins[lo_b]; vah = bins[hi_b + 1]
    return poc, vah, val, lo, hi


def va_fade(m1, bin_size, cost_r=0.05, stop_buf=0.25):
    """Fade a tap of the PRIOR day's value-area edge back to its POC. Short at VAH,
    long at VAL; target = POC; stop = beyond the prior-day extreme by stop_buf*range."""
    dord = m1.index.normalize().asi8
    uniq, starts = np.unique(dord, return_index=True); starts = list(starts) + [len(m1)]
    profiles = {}
    items = []
    for di in range(len(uniq)):
        a, b = starts[di], starts[di + 1]
        day = uniq[di]
        dm1 = m1.iloc[a:b]
        prof = day_profile(dm1, bin_size)
        if prof is not None:
            profiles[day] = prof
        if di == 0:
            continue
        prev = profiles.get(uniq[di - 1])
        if prev is None:
            continue
        poc, vah, val, plo, phi = prev
        H = dm1["high"].values; L = dm1["low"].values; C = dm1["close"].values; ix = dm1.index
        # first touch of VAH (short) or VAL (long)
        d = ent = stop = None
        for j in range(len(dm1)):
            if H[j] >= vah and ent is None:
                d, ent, stop = -1, vah, phi + stop_buf * (phi - plo)
                start_j = j; break
            if L[j] <= val and ent is None:
                d, ent, stop = 1, val, plo - stop_buf * (phi - plo)
                start_j = j; break
        if ent is None:
            continue
        tgt = poc; risk = abs(ent - stop)
        if risk <= 0:
            continue
        pnl = None
        for j in range(start_j, len(dm1)):
            if d == 1:
                if L[j] <= stop: pnl = -1 - cost_r; break
                if H[j] >= tgt: pnl = (tgt - ent) / risk - cost_r; break
            else:
                if H[j] >= stop: pnl = -1 - cost_r; break
                if L[j] <= tgt: pnl = (ent - tgt) / risk - cost_r; break
        if pnl is None:
            pnl = (d * (C[-1] - ent)) / risk - cost_r
        items.append((ix[start_j], pnl))
    return items


def main():
    xau = load_m1_vol("XAUUSD"); nas = load_m1_vol("NAS100")
    print(f"data: XAU {len(xau):,}  NAS {len(nas):,}  (PnL in R; IS<{CUT.date()}<=OOS)\n")

    print("=== 1. INITIAL BALANCE breakout (IB=60m, risk=IB range) ===")
    for tp in (1.0, 2.0, 3.0):
        it, nd, nt = ib_breakout(nas, _nas_open_min, ib_min=60, tp_mult=tp)
        _report(f"NAS NY-IB tp{tp}", it, extra=f"{100*nt/nd:.0f}%hari")
    for tp in (1.0, 2.0, 3.0):
        it, nd, nt = ib_breakout(xau, lambda d: 8 * 60, ib_min=60, tp_mult=tp, end_min=16 * 60)
        _report(f"XAU London-IB tp{tp}", it, extra=f"{100*nt/nd:.0f}%hari")
    for tp in (1.0, 2.0):
        it, nd, nt = ib_breakout(xau, _nas_open_min, ib_min=60, tp_mult=tp, end_min=20 * 60)
        _report(f"XAU NY-IB tp{tp}", it, extra=f"{100*nt/nd:.0f}%hari")

    print("\n=== 2. VALUE AREA fade (prior-day POC/VAH/VAL -> fade edge to POC) ===")
    for bs in (1.0, 2.0):
        _report(f"XAU VAfade bin{bs}", va_fade(xau, bs))
    for bs in (5.0, 10.0):
        _report(f"NAS VAfade bin{bs}", va_fade(nas, bs))


if __name__ == "__main__":
    main()
