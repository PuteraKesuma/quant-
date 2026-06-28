"""Skeptical re-audit of the live strategy slots (config.yaml -> live.strategies).

Premise: treat every previously-claimed backtest number as WRONG until it is
reproduced here from committed, runnable code. Born out of the mr_xau finding,
where ~half the signals produced an UNFILLABLE order (stop on the wrong side of
entry) that the old scratch validators booked as PROFIT.

For each backtestable slot this prints:
  - reproduced PF (IS / OOS), trade count, win rate, max DD
  - % of signals that produce an INVALID order (SL on the wrong side of the
    actual fill) -- those are NOT tradable and are excluded from the honest run
  - honest intrabar fill on M1 with SL-checked-before-TP (pessimistic tie-break),
    realistic cost + one cost level higher
  - per-year PF (green-year count), and a bootstrap Monte-Carlo 5th-pct PF

Data : data/Level_0_Raw/<SYM>_1m.duckdb  (Dukascopy 1m, UTC, 2021-2026)
Deps : duckdb, pandas, numpy  (NO pytz -- index is built from epoch seconds)

Run  : python research/audit_live_strategies.py
"""
import sys
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT))
from pipeline.backtest.strategy_zrev import (  # committed, audited Z code
    simulate as zrev_simulate, ZRevParams, resample_1h as zrev_resample_1h,
)

CUT = pd.Timestamp("2025-01-01", tz="UTC")      # IS < CUT <= OOS  (matches old MR split)


# --------------------------------------------------------------------------- data
def load_m1(sym: str) -> pd.DataFrame:
    """1m OHLC, tz-aware UTC index, from the project's duckdb (no pytz path)."""
    db = ROOT / "data" / "Level_0_Raw" / f"{sym}_1m.duckdb"
    con = duckdb.connect(str(db), read_only=True)
    rows = con.execute("SELECT epoch(ts), open, high, low, close FROM ohlcv ORDER BY ts").fetchall()
    con.close()
    a = np.asarray(rows, dtype="float64")
    idx = pd.to_datetime(a[:, 0], unit="s", utc=True)
    return pd.DataFrame({"open": a[:, 1], "high": a[:, 2], "low": a[:, 3], "close": a[:, 4]}, index=idx)


def to_h1(m1: pd.DataFrame) -> pd.DataFrame:
    return (m1.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
              .dropna(subset=["open"]))


def to_d1(m1: pd.DataFrame) -> pd.DataFrame:
    return (m1.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"})
              .dropna(subset=["open"]))


# ------------------------------------------------------------------------- metrics
def stats(pnls) -> dict:
    d = np.asarray(pnls, dtype=float)
    if len(d) == 0:
        return dict(n=0, pf=float("nan"), net=0.0, wr=float("nan"), maxdd=0.0)
    w = d[d > 0].sum(); l = -d[d < 0].sum()
    eq = np.cumsum(d); mdd = float((eq - np.maximum.accumulate(eq)).min())
    return dict(n=len(d), pf=(w / l if l > 0 else float("inf")),
                net=float(d.sum()), wr=100 * float((d > 0).mean()), maxdd=mdd)


def fmt(s: dict) -> str:
    pf = "inf " if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return f"n={s['n']:4d} PF={pf:>4} net={s['net']:+8.1f} wr={s['wr']:4.0f}% maxDD={s['maxdd']:+8.1f}"


def split(items):
    """items: list[(ts, pnl)] -> (is_pnls, oos_pnls)."""
    return ([p for t, p in items if t < CUT], [p for t, p in items if t >= CUT])


def per_year(items):
    if not items:
        return {}
    s = pd.Series([p for _, p in items], index=pd.DatetimeIndex([t for t, _ in items]))
    out = {}
    for yr, g in s.groupby(s.index.year):
        l = -g[g < 0].sum()
        out[int(yr)] = (round(g[g > 0].sum() / l, 2) if l > 0 else float("inf"), round(float(g.sum()), 1), len(g))
    return out


def mc_pf_p5(pnls, iters=2000, seed=0) -> float:
    """Bootstrap (resample trades with replacement) 5th-percentile profit factor."""
    d = np.asarray(pnls, float)
    if len(d) < 10:
        return float("nan")
    rng = np.random.default_rng(seed)
    pfs = []
    for _ in range(iters):
        s = rng.choice(d, size=len(d), replace=True)
        l = -s[s < 0].sum()
        if l > 0:
            pfs.append(s[s > 0].sum() / l)
    return float(np.percentile(pfs, 5)) if pfs else float("nan")


def report(name, items, invalid, total_signals, cost_levels=None, h1=None, m1=None, rerun=None):
    is_, oos = split(items)
    print(f"\n{'='*86}\n{name}\n{'='*86}")
    if total_signals:
        print(f"  signals={total_signals}  INVALID(unfillable)={invalid} "
              f"({100*invalid/total_signals:.0f}%)  tradable={total_signals-invalid}")
    print(f"  ALL : {fmt(stats([p for _, p in items]))}")
    print(f"  IS  : {fmt(stats(is_))}")
    print(f"  OOS : {fmt(stats(oos))}")
    py = per_year(items)
    green = sum(1 for v in py.values() if v[0] != float("inf") and v[0] >= 1.0 or v[0] == float("inf"))
    print(f"  per-year (PF/net/n): " + "  ".join(
        f"{yr}:{(pf if pf!=float('inf') else 9.99):.2f}/{net:+.0f}/{n}" for yr, (pf, net, n) in py.items()))
    print(f"  green years: {green}/{len(py)}")
    print(f"  MonteCarlo OOS PF 5th-pct: {mc_pf_p5(oos):.2f}")
    if cost_levels and rerun is not None:
        print("  cost sensitivity (OOS PF):")
        for c in cost_levels:
            it2, inv2, tot2 = rerun(c)
            _, o2 = split(it2)
            print(f"    cost {c:>4}: {fmt(stats(o2))}")


# ===================================================================== MR (mean-rev)
def mr_buggy(h1, N=20, ez=2.5, sz=3.0, max_hold=48, cost=0.30):
    """EXACT reproduction of the old scratch validator (mr_validate / m1_validate):
    books `stop-entry` on a stop hit with NO check that the stop is below entry.
    When |z| >= sz the stop sits ABOVE a long's entry, so `stop-entry` is POSITIVE
    -> a losing/unfillable trade is recorded as a WIN. This is the artifact."""
    c = h1["close"].values; hi = h1["high"].values; lo = h1["low"].values; idx = h1.index
    ma = pd.Series(c).rolling(N).mean().shift(1).values
    sd = pd.Series(c).rolling(N).std().shift(1).values
    tr = []; pos = 0; entry = tgt = stop = 0.0; ei = 0
    for i in range(len(h1)):
        if np.isnan(ma[i]) or np.isnan(sd[i]) or sd[i] <= 0:
            continue
        if pos == 0:
            z = (c[i] - ma[i]) / sd[i]
            if z <= -ez:   pos, entry, ei, tgt, stop = 1, c[i], i, ma[i], ma[i] - sz * sd[i]
            elif z >= ez:  pos, entry, ei, tgt, stop = -1, c[i], i, ma[i], ma[i] + sz * sd[i]
        elif pos == 1:
            if lo[i] <= stop:   tr.append((idx[i], stop - entry - cost)); pos = 0
            elif hi[i] >= tgt:  tr.append((idx[i], tgt - entry - cost)); pos = 0
            elif i - ei >= max_hold: tr.append((idx[i], c[i] - entry - cost)); pos = 0
        else:
            if hi[i] >= stop:   tr.append((idx[i], entry - stop - cost)); pos = 0
            elif lo[i] <= tgt:  tr.append((idx[i], entry - tgt - cost)); pos = 0
            elif i - ei >= max_hold: tr.append((idx[i], entry - c[i] - cost)); pos = 0
    return tr


def mr_signals(h1, N=20, ez=2.5):
    c = h1["close"].values
    ma = pd.Series(c).rolling(N).mean().shift(1).values   # no-lookahead: prior N completed bars
    sd = pd.Series(c).rolling(N).std().shift(1).values
    out = []
    for i in range(len(h1)):
        if np.isnan(ma[i]) or np.isnan(sd[i]) or sd[i] <= 0:
            continue
        z = (c[i] - ma[i]) / sd[i]
        if z <= -ez:   out.append((h1.index[i], 1, ma[i], sd[i], z))
        elif z >= ez:  out.append((h1.index[i], -1, ma[i], sd[i], z))
    return out


def mr_honest(m1, h1, N=20, ez=2.5, sz=3.0, max_hold=48, cost=0.30):
    """Honest MR: enter at MARKET on the next bar open after the completed H1 signal
    (this is what the live slot does), drop any order whose SL is on the wrong side
    of the *actual* fill, resolve on M1 with SL-before-TP, time-exit at max_hold."""
    sigs = mr_signals(h1, N, ez)
    trades = []; invalid = 0
    busy_until = pd.Timestamp.min.tz_localize("UTC")
    for label, d, ma, sd, z in sigs:
        entry_start = label + pd.Timedelta(hours=1)        # market fill ~ next bar
        if entry_start < busy_until:                       # single position at a time
            continue
        seg = m1.loc[entry_start:entry_start + pd.Timedelta(hours=max_hold)]
        if len(seg) == 0:
            continue
        entry = float(seg["open"].iloc[0])
        tgt = ma
        stop = ma - sz * sd if d == 1 else ma + sz * sd
        # executability against the ACTUAL fill (BUY: SL<entry<TP ; SELL: SL>entry>TP)
        ok = (stop < entry < tgt) if d == 1 else (stop > entry > tgt)
        if not ok:
            invalid += 1
            continue
        H = seg["high"].values; L = seg["low"].values; C = seg["close"].values; ix = seg.index
        pnl = None; xts = ix[-1]
        for j in range(len(seg)):
            if d == 1:
                if L[j] <= stop:  pnl = stop - entry - cost; xts = ix[j]; break   # SL first
                if H[j] >= tgt:   pnl = tgt - entry - cost; xts = ix[j]; break
            else:
                if H[j] >= stop:  pnl = entry - stop - cost; xts = ix[j]; break
                if L[j] <= tgt:   pnl = entry - tgt - cost; xts = ix[j]; break
        if pnl is None:                                    # time-exit at market
            pnl = d * (C[-1] - entry) - cost; xts = ix[-1]
        trades.append((xts, pnl)); busy_until = xts
    return trades, invalid, len(sigs)


# ====================================================================== ZREV (Z)
def zrev_audit(m1, cost=0.30):
    h1 = zrev_resample_1h(m1.assign(volume=0))
    trades = zrev_simulate(h1, ZRevParams(donchian_n=100, exit_n=20, cost_points=cost))
    items = [(t.entry_ts, t.pnl_points) for t in trades]
    # Executability: the live broker SL is the OPPOSITE exit channel -- for a long
    # SL=exit_dn (below entry), for a short SL=exit_up (above entry). Correct side by
    # construction, so 0 invalid orders. (Verified structurally, not booked as PnL.)
    return items, 0, len(trades)


# ====================================================================== NAS100 ORB
def _nas_open_min(day_date) -> int:
    """UTC opening minute of the NAS cash session, DST-aware (live `dst_open`)."""
    et = dt.datetime(day_date.year, day_date.month, day_date.day, 12, tzinfo=ZoneInfo("America/New_York"))
    base = 13 * 60 + 30
    return base + 60 if et.dst() == dt.timedelta(0) else base   # winter: open 1h later in UTC


def nas_orb(m1, range_min=30, tp_mult=1.0, sl_mult=1.0, trend_sma=50,
            breakeven_r=0.5, session_end_min=20 * 60, cost_points=2.0):
    """Full reproduction of the LIVE orb30_nas logic: DST-aware open, first-breakout
    entry at the range edge, 1:1 TP/SL, daily-SMA50 trend filter (no-lookahead),
    0.5R breakeven, 20:00 UTC time-exit. M1 intrabar, SL-before-TP, mirrors the
    exact check order in ORBStrategy._exit_hit (BE armed -> BE -> SL -> TP)."""
    d1 = to_d1(m1)
    dclose = d1["close"]
    sma = dclose.rolling(trend_sma).mean()
    prev_close = dclose.shift(1)        # trade on date D uses closes up to D-1 only
    prev_sma = sma.shift(1)
    trend_by_date = {}
    for ts in d1.index:
        pc, ps = prev_close.loc[ts], prev_sma.loc[ts]
        trend_by_date[ts.date()] = 0 if (np.isnan(pc) or np.isnan(ps)) else (1 if pc > ps else (-1 if pc < ps else 0))

    H = m1["high"].values; L = m1["low"].values; C = m1["close"].values
    mod = m1.index.hour.values * 60 + m1.index.minute.values
    day_ord = m1.index.normalize().asi8
    uniq, starts = np.unique(day_ord, return_index=True)
    starts = list(starts) + [len(m1)]

    items = []; invalid = 0; n_breakout = 0; n_trend_skip = 0
    for di in range(len(uniq)):
        a, b = starts[di], starts[di + 1]
        day_date = m1.index[a].date()
        omin = _nas_open_min(day_date)
        modd = mod[a:b]
        idx = np.arange(a, b)
        rmask = (modd >= omin) & (modd < omin + range_min)
        if rmask.sum() < range_min // 2:
            continue
        ridx = idx[rmask]
        orb_high = H[ridx].max(); orb_low = L[ridx].min()
        size = orb_high - orb_low
        if size <= 0:
            continue
        pidx = idx[modd >= omin + range_min]
        entry_i = direction = entry = None
        for i in pidx:
            if H[i] > orb_high:  entry_i, direction, entry = i, 1, orb_high; break
            if L[i] < orb_low:   entry_i, direction, entry = i, -1, orb_low; break
        if entry_i is None:
            continue
        n_breakout += 1
        td = trend_by_date.get(day_date, 0)                  # trend filter (FLAT on disagree)
        if td == 0 or (td > 0) != (direction == 1):
            n_trend_skip += 1
            continue
        tp = entry + direction * size * tp_mult
        sl = entry - direction * size * sl_mult
        ok = (sl < entry < tp) if direction == 1 else (sl > entry > tp)
        if not ok:                                            # 1:1 stop is always valid; guard anyway
            invalid += 1
            continue
        risk = size * sl_mult
        cost_r = cost_points / risk
        armed = False; pnl = None; xts = None
        for j in range(entry_i, b):
            if session_end_min is not None and mod[j] >= session_end_min:
                pnl = direction * (C[j] - entry) / risk - cost_r; xts = m1.index[j]; break
            if direction == 1:
                if breakeven_r is not None and not armed and (H[j] - entry) >= breakeven_r * risk:
                    armed = True
                if armed and L[j] <= entry:           pnl = -cost_r; xts = m1.index[j]; break          # BE
                if L[j] <= sl:                        pnl = -1.0 - cost_r; xts = m1.index[j]; break     # SL
                if H[j] >= tp:                        pnl = tp_mult / sl_mult - cost_r; xts = m1.index[j]; break  # TP
            else:
                if breakeven_r is not None and not armed and (entry - L[j]) >= breakeven_r * risk:
                    armed = True
                if armed and H[j] >= entry:           pnl = -cost_r; xts = m1.index[j]; break
                if H[j] >= sl:                        pnl = -1.0 - cost_r; xts = m1.index[j]; break
                if L[j] <= tp:                        pnl = tp_mult / sl_mult - cost_r; xts = m1.index[j]; break
        if pnl is None:
            pnl = direction * (C[b - 1] - entry) / risk - cost_r; xts = m1.index[b - 1]
        items.append((xts, pnl))
    print(f"  [nas debug] breakouts={n_breakout}  trend-skipped={n_trend_skip}  taken={len(items)}")
    return items, invalid, n_breakout


# =============================================================================== main
def main():
    print(f"AUDIT  cutoff IS<{CUT.date()}<=OOS   (PnL: XAU in $ per 0.01 lot; NAS in R)")

    xau = load_m1("XAUUSD")
    nas = load_m1("NAS100")
    xau_h1 = to_h1(xau)
    print(f"XAUUSD m1={len(xau):,} h1={len(xau_h1):,}   NAS100 m1={len(nas):,}")

    # ---- mr_xau ----
    buggy = mr_buggy(xau_h1)
    bi, bo = split(buggy)
    print(f"\n### mr_xau — OLD (BUGGY) VALIDATOR reproduction (the source of the claim)")
    print(f"  ALL : {fmt(stats([p for _, p in buggy]))}")
    print(f"  IS  : {fmt(stats(bi))}")
    print(f"  OOS : {fmt(stats(bo))}   <-- this is the bogus 'OOS PF 2.7-3.2'")
    mh, minv, mtot = mr_honest(xau, xau_h1)
    report("mr_xau — HONEST (drop unfillable orders, M1 fill, SL-first)",
           mh, minv, mtot, cost_levels=[0.30, 0.60],
           rerun=lambda c: mr_honest(xau, xau_h1, cost=c))

    # ---- zrev_xau ----
    zit, zinv, ztot = zrev_audit(xau)
    report("zrev_xau — Donchian S&R entry100/exit20 (committed strategy_zrev.simulate)",
           zit, zinv, ztot, cost_levels=[0.30, 0.60],
           rerun=lambda c: zrev_audit(xau, cost=c))

    # ---- orb30_nas ----
    nit, ninv, ntot = nas_orb(nas)
    report("orb30_nas — DST open + SMA50 trend + 0.5R breakeven, 1:1 (full live logic)",
           nit, ninv, ntot, cost_levels=[2.0, 4.0],
           rerun=lambda c: nas_orb(nas, cost_points=c))

    print(f"\n{'='*86}\nvision_smc_xau — discretionary LLM (Claude vision SMC). NOT backtestable:\n"
          f"  decisions depend on live chart images + model judgement; no historical\n"
          f"  signal series can be regenerated deterministically -> UNVERIFIED by\n"
          f"  construction. No PF can be reproduced.\n{'='*86}")


if __name__ == "__main__":
    main()
