"""SUPERTREND flat-limit LONG-only on XAUUSD spot — VPS validation of a new research candidate.

Run on the VPS:   python research/supertrend_long_xau.py

Origin: a user Pine strategy. Supertrend(src=hl2, ATR period=21, mult=5.5, Wilder RMA ATR). When the trailing
SUPPORT band is FLAT for 3 bars in an uptrend (`flat_up`) it places a BUY LIMIT at that band (a pullback-to-
support long); the mirror `flat_dn` places a SELL LIMIT. TP 26000 pts / SL 13000 pts (mintick 0.01 => $260/$130,
RR 2:1); one position at a time.

Local finding (laptop, 2019-12..2026-06 XAU 1m): the BOTH-SIDES version is mediocre (H1 PF 1.16) because the
SHORT side is dead (PF 0.53) and drags it down. LONG-ONLY (`flat_up` only) is robust and the first NEW idea this
ideation cycle to pass the full battery: H1 PF 2.15, WR 63%, 6/7 green, OOS 2.85, MC5 1.31, cost-insensitive to
$5, param-plateau — AND daily-PnL corr with live Z-XAU is only +0.04 (a genuine diversifier). The edge is
"long gold when Supertrend is up + fixed 2:1 RR", not the flat-3-bar gimmick (market-buy every uptrend bar is
also robust, PF 1.70). Trades LONG SPOT XAU => no futures feed needed, unlike VP-MGC.

This script reuses the audit helpers (load_m1, stats, split, per_year, mc_pf_p5, CUT). It re-validates on the
VPS's own DuckDB so you can confirm the numbers with the live data, then decide on a paper forward-test slot.
Honest M1 first-touch fill (SL wins same-bar ties); limit valid only while the Supertrend trend persists.
"""
import sys
sys.path.insert(0, r"C:\Quant")
sys.path.insert(0, r"C:\Quant\research")
import numpy as np, pandas as pd
from audit_live_strategies import load_m1, stats, split, per_year, mc_pf_p5, CUT

MINTICK = 0.01

# --- load XAU 1m once; keep raw M1 arrays for honest fill ---
M1 = load_m1("XAUUSD")
M1_IDX = M1.index.values
M1_HI = M1["high"].values; M1_LO = M1["low"].values; M1_CL = M1["close"].values
print(f"XAUUSD 1m: {M1.index[0]} -> {M1.index[-1]}  ({len(M1):,} bars)   IS/OOS cut = {CUT.date()}")


def resample(tf):
    return M1.resample(tf).agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(subset=["open"])


def wilder_atr(h, l, c, n):
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    atr = np.full(len(c), np.nan); atr[n - 1] = tr[:n].mean()
    for i in range(n, len(c)):
        atr[i] = (atr[i - 1] * (n - 1) + tr[i]) / n
    return atr


def supertrend(r, period=21, mult=5.5):
    h, l, c = r["high"].values, r["low"].values, r["close"].values
    src = (h + l) / 2.0; atr = wilder_atr(h, l, c, period)
    N = len(c); up = np.full(N, np.nan); dn = np.full(N, np.nan); trend = np.ones(N, int)
    for i in range(N):
        if not np.isfinite(atr[i]):
            up[i] = src[i]; dn[i] = src[i]; trend[i] = 1; continue
        bu = src[i] - mult * atr[i]; bd = src[i] + mult * atr[i]
        up1 = up[i - 1] if i > 0 and np.isfinite(up[i - 1]) else bu
        dn1 = dn[i - 1] if i > 0 and np.isfinite(dn[i - 1]) else bd
        up[i] = max(bu, up1) if (i > 0 and c[i - 1] > up1) else bu
        dn[i] = min(bd, dn1) if (i > 0 and c[i - 1] < dn1) else bd
        t = trend[i - 1] if i > 0 else 1
        if t == -1 and c[i] > dn1: t = 1
        elif t == 1 and c[i] < up1: t = -1
        trend[i] = t
    return up, dn, trend, atr


def next_flip(trend, idx):
    """Timestamp at which trend NEXT changes (= limit-order expiry)."""
    N = len(trend); nxt = np.array([None] * N, dtype=object); f = None
    for i in range(N - 1, -1, -1):
        nxt[i] = f
        if i > 0 and trend[i] != trend[i - 1]: f = idx[i]
    return nxt


def fill_limit(t0, t1, direction, level):
    s = np.searchsorted(M1_IDX, np.datetime64(t0))
    e = np.searchsorted(M1_IDX, np.datetime64(t1)) if t1 is not None else len(M1_IDX)
    if e <= s: return None
    hit = np.where(M1_LO[s:e] <= level)[0] if direction == 1 else np.where(M1_HI[s:e] >= level)[0]
    return M1_IDX[s + hit[0]] if len(hit) else None


def exit_tpsl(t_fill, direction, entry, sl, tp, cost, max_h=240):
    risk = abs(entry - sl)
    if risk <= 0: return None, None
    s = np.searchsorted(M1_IDX, np.datetime64(t_fill))
    e = np.searchsorted(M1_IDX, np.datetime64(pd.Timestamp(t_fill) + pd.Timedelta(hours=max_h)))
    hi, lo, cl = M1_HI[s:e], M1_LO[s:e], M1_CL[s:e]
    if len(cl) == 0: return None, None
    if direction == 1: sh = np.where(lo <= sl)[0]; th = np.where(hi >= tp)[0]
    else: sh = np.where(hi >= sl)[0]; th = np.where(lo <= tp)[0]
    fs = sh[0] if len(sh) else 10**9; ft = th[0] if len(th) else 10**9
    if fs == 10**9 and ft == 10**9: return ((cl[-1] - entry) * direction - cost) / risk, M1_IDX[s + len(cl) - 1]
    if fs <= ft: return (-risk - cost) / risk, M1_IDX[s + fs]
    return (abs(tp - entry) - cost) / risk, M1_IDX[s + ft]


def run(tf="1h", period=21, mult=5.5, tp_pts=26000, sl_pts=13000, cost=0.5,
        both_sides=False, mode="flat"):
    """Returns list[(exit_ts, R)]. mode: flat=user flat-limit; market=buy every uptrend-bar open;
    atrpull=limit at close-0.5*ATR. both_sides adds the (dead) short leg for comparison."""
    r = resample(tf); idx = r.index
    up, dn, trend, atr = supertrend(r, period, mult)
    O = r["open"].values; C = r["close"].values
    flip = next_flip(trend, idx.values); tp_d = tp_pts * MINTICK; sl_d = sl_pts * MINTICK
    out = []; free = None
    for i in range(3, len(r) - 1):
        if free is not None and np.datetime64(idx[i]) < np.datetime64(free): continue
        direction = 0; level = None
        if trend[i] == 1:
            if mode == "flat":
                if up[i] == up[i - 1] == up[i - 2]: direction = 1; level = up[i]
            elif mode == "market":
                direction = 1
            elif mode == "atrpull" and np.isfinite(atr[i]):
                direction = 1; level = C[i] - 0.5 * atr[i]
        elif both_sides and trend[i] == -1 and mode == "flat":
            if dn[i] == dn[i - 1] == dn[i - 2]: direction = -1; level = dn[i]
        if direction == 0: continue
        if level is None:                      # market entry at next open
            t_fill = idx[i + 1]; entry = O[i + 1]
        else:
            t_fill = fill_limit(idx[i + 1], flip[i], direction, level)
            if t_fill is None: continue
            entry = level
        if direction == 1: sl = entry - sl_d; tp = entry + tp_d
        else:              sl = entry + sl_d; tp = entry - tp_d
        rr, tx = exit_tpsl(t_fill, direction, entry, sl, tp, cost)
        if rr is None: continue
        out.append((pd.Timestamp(tx).tz_localize("UTC"), rr)); free = tx   # tz-aware for split/per_year
    return out


def report(items, label):
    if len(items) < 15:
        print(f"  {label:<34} n {len(items)} (few)"); return
    all_p = [p for _, p in items]; is_, oos = split(items)
    py = per_year(items); green = sum(1 for v in py.values() if v[0] == float("inf") or v[0] >= 1.0)
    s = stats(all_p)
    print(f"  {label:<34} n {s['n']:>4} WR {s['wr']:3.0f}% PF {s['pf']:4.2f} net {s['net']:+7.1f}R "
          f"green {green}/{len(py)} OOS-PF {stats(oos)['pf']:4.2f} MC5 {mc_pf_p5(all_p):4.2f}")


if __name__ == "__main__":
    print("\n=== TIMEFRAME SWEEP  LONG-only flat-limit (ATR21x5.5, tp26000/sl13000, cost$0.5) ===")
    for tf in ("15min", "30min", "1h", "2h", "4h"):
        report(run(tf=tf), f"{tf} LONG-only")

    print("\n=== BOTH-SIDES vs LONG-only vs SHORT-only (1h) — why long-only ===")
    both = run(tf="1h", both_sides=True)
    report(both, "1h BOTH sides (Pine as-is)")
    report(run(tf="1h"), "1h LONG-only (flat_up)")

    print("\n=== does the FLAT-3-bar gimmick ADD value? (1h long-only) ===")
    report(run(tf="1h", mode="flat"),    "flat-limit (user)")
    report(run(tf="1h", mode="market"),  "market-buy every uptrend bar")
    report(run(tf="1h", mode="atrpull"), "naive 0.5*ATR pullback limit")

    print("\n=== 1h LONG-only: per-year, cost stress, RR & param plateau ===")
    b = run(tf="1h")
    s = pd.Series([p for _, p in b], index=pd.DatetimeIndex([t for t, _ in b]))
    print("  per-year R:", {int(k): round(v, 1) for k, v in s.groupby(s.index.year).sum().items()})
    for c in (0.5, 1.0, 2.0, 5.0):
        report(run(tf="1h", cost=c), f"cost=${c}")
    for tp, sl in ((26000, 13000), (20000, 10000), (30000, 15000), (13000, 13000)):
        report(run(tf="1h", tp_pts=tp, sl_pts=sl), f"tp{tp} sl{sl} RR{tp/sl:.1f}")
    for p, m in ((21, 5.5), (14, 3.0), (10, 3.0), (21, 4.0)):
        report(run(tf="1h", period=p, mult=m), f"ATR{p}x{m}")

    print("\nNote: R = risk units (fixed SL). LONG-only is the deployable form; short side is dead — do not trade it.")
