"""Strategy research lab: screen many intraday strategies under a fixed RR 1:2.

Goal: find an edge with **win-rate > 50% at reward:risk = 1:2** (TP = 2x SL).
Every strategy only has to define (entry bar, direction, SL distance). A single
shared exit simulator then sets TP = 2x SL and races SL vs TP bar-by-bar
(SL checked first = conservative) within a 1-day horizon. So all strategies are
compared on identical, fair exit rules.

R is the SL distance, so expectancy/PF are normalised across strategies & symbols.
NOTE: screening study — no spread/commission, entries at signal-bar close. Treat
results as an optimistic upper bound for ranking ideas, not live P&L.

    python -m pipeline.analysis.strategy_lab
"""
import itertools
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent.parent
SYMBOLS = ["NAS100", "XAUUSD"]          # focus per user request
RR_GRID = [0.5, 1.0, 1.5, 2.0]          # reward:risk sweep -> TP = rr * SL


# ----------------------------------------------------------------------------- data
def load_m1(symbol: str) -> pd.DataFrame:
    db = ROOT / "data" / "Level_0_Raw" / f"{symbol}_1m.duckdb"
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone='UTC'")
    cols = [r[1] for r in con.execute("PRAGMA table_info('ohlcv')").fetchall()]
    where = "WHERE NOT is_synthetic" if "is_synthetic" in cols else ""
    df = con.execute(f"SELECT ts,open,high,low,close FROM ohlcv {where} ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    o = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return o


# ------------------------------------------------------------------- indicators
def ema(s, n):  return s.ewm(span=n, adjust=False).mean()
def rsi(s, n):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))
def atr(df, n):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


# ------------------------------------------------------------------- exit engine
def simulate(df: pd.DataFrame, entries: list, horizon: int, rr: float) -> pd.DataFrame:
    """entries: list of (pos, dir(+1/-1), sl_dist). TP = rr*SL. Returns pnl in R."""
    H = df["high"].values; L = df["low"].values; C = df["close"].values
    n = len(df)
    rows = []
    for pos, d, sl_dist in entries:
        if sl_dist <= 0 or pos + 1 >= n:
            continue
        entry = C[pos]
        tp = entry + rr * sl_dist * d
        sl = entry - sl_dist * d
        end = min(pos + 1 + horizon, n)
        hh = H[pos + 1:end]; ll = L[pos + 1:end]
        if d > 0:
            sl_hit = np.where(ll <= sl)[0]
            tp_hit = np.where(hh >= tp)[0]
        else:
            sl_hit = np.where(hh >= sl)[0]
            tp_hit = np.where(ll <= tp)[0]
        si = sl_hit[0] if len(sl_hit) else 10**9
        ti = tp_hit[0] if len(tp_hit) else 10**9
        if si == ti == 10**9:
            pnl = (C[end - 1] - entry) * d / sl_dist          # timeout: mark-to-close
            reason = "TIME"
        elif si <= ti:                                         # SL first (conservative on ties)
            pnl, reason = -1.0, "SL"
        else:
            pnl, reason = rr, "TP"
        rows.append((pos, d, pnl, reason))
    return pd.DataFrame(rows, columns=["pos", "dir", "pnl_R", "reason"])


def first_per_day(df, mask):
    """Keep only the first True per UTC date (max one trade/day)."""
    m = mask.copy()
    day = df.index.normalize()
    seen = set(); out = np.zeros(len(df), dtype=bool)
    idx = np.where(m.values)[0]
    for i in idx:
        dkey = day[i]
        if dkey in seen: continue
        seen.add(dkey); out[i] = True
    return out


def hod(df):  return df.index.hour * 60 + df.index.minute   # minute-of-day UTC


# ------------------------------------------------------------------- strategies
# each returns (tf_df, entries list, horizon)
def s_orb_break(m1, fade=False):
    entries = []
    for date, day in m1.groupby(m1.index.date):
        start = pd.Timestamp(str(date), tz="UTC").replace(hour=13, minute=30)
        rend = start + pd.Timedelta(minutes=30)
        send = start.replace(hour=20)
        win = day[(day.index >= start) & (day.index < rend)]
        if len(win) < 15: continue
        rh, rl = win["high"].max(), win["low"].min(); size = rh - rl
        if size <= 0: continue
        post = day[(day.index >= rend) & (day.index <= send)]
        for ts, b in post.iterrows():
            d = 0
            if b["high"] > rh: d = 1
            elif b["low"] < rl: d = -1
            if d:
                if fade: d = -d
                entries.append((m1.index.get_loc(ts), d, size))
                break
    return m1, entries, 1440

def s_firstcandle(m1):
    entries = []
    for date, day in m1.groupby(m1.index.date):
        c0 = pd.Timestamp(str(date), tz="UTC").replace(hour=13, minute=30)
        c1 = c0 + pd.Timedelta(minutes=5)
        w = day[(day.index >= c0) & (day.index < c1)]
        if len(w) < 3: continue
        d = 1 if w["close"].iloc[-1] >= w["open"].iloc[0] else -1
        rng = w["high"].max() - w["low"].min()
        nxt = day[day.index >= c1]
        if rng <= 0 or nxt.empty: continue
        entries.append((m1.index.get_loc(nxt.index[0]), d, rng))
    return m1, entries, 1440

def s_pdh_pdl(m1):
    entries = []
    daily = m1.resample("1D").agg({"high": "max", "low": "min"})
    pdh = daily["high"].shift(); pdl = daily["low"].shift()
    for date, day in m1.groupby(m1.index.date):
        key = pd.Timestamp(str(date), tz="UTC")
        if key not in pdh.index or np.isnan(pdh.loc[key]): continue
        h, l = pdh.loc[key], pdl.loc[key]; size = (h - l) * 0.5
        if size <= 0: continue
        act = day[(hod(day) >= 480) & (hod(day) <= 1200)]   # 08:00-20:00
        for ts, b in act.iterrows():
            d = 0
            if b["high"] > h: d = 1
            elif b["low"] < l: d = -1
            if d:
                entries.append((m1.index.get_loc(ts), d, size)); break
    return m1, entries, 1440

def s_donchian(m15):
    n = 20
    hh = m15["high"].rolling(n).max().shift()
    ll = m15["low"].rolling(n).min().shift()
    a = atr(m15, 14)
    long = (m15["close"] > hh) & a.notna()
    short = (m15["close"] < ll) & a.notna()
    entries = _from_masks(m15, long, short, a)
    return m15, entries, 96

def s_rsi2(m15):
    r = rsi(m15["close"], 2); a = atr(m15, 14)
    long = (r < 5) & a.notna(); short = (r > 95) & a.notna()
    return m15, _from_masks(m15, long, short, a), 96

def s_bb_revert(m15):
    ma = m15["close"].rolling(20).mean(); sd = m15["close"].rolling(20).std()
    a = atr(m15, 14)
    long = (m15["close"] < ma - 2 * sd) & a.notna()
    short = (m15["close"] > ma + 2 * sd) & a.notna()
    return m15, _from_masks(m15, long, short, a), 96

def s_ma_pullback(m15):
    e50, e200, a = ema(m15["close"], 50), ema(m15["close"], 200), atr(m15, 14)
    up = (m15["close"] > e200) & (m15["low"] <= e50) & (m15["close"] > e50) & a.notna()
    dn = (m15["close"] < e200) & (m15["high"] >= e50) & (m15["close"] < e50) & a.notna()
    return m15, _from_masks(m15, up, dn, a), 96

def s_vwap_revert(m5):
    df = m5.copy()
    day = df.index.normalize()
    tp = (df["high"] + df["low"] + df["close"]) / 3
    cum = tp.groupby(day).cumsum(); cnt = tp.groupby(day).cumcount() + 1
    vwap = cum / cnt
    dev = df["close"] - vwap
    sd = dev.groupby(day).transform(lambda x: x.expanding().std())
    long = (dev < -2 * sd) & sd.notna() & (sd > 0)
    short = (dev > 2 * sd) & sd.notna() & (sd > 0)
    return df, _from_masks(df, long, short, sd), 288

def s_gap_fade(m1):
    entries = []
    daily_close = m1.resample("1D").agg({"close": "last"})["close"].shift()
    a = atr(m1.resample("1D").agg({"high": "max", "low": "min", "close": "last"}), 14)
    for date, day in m1.groupby(m1.index.date):
        key = pd.Timestamp(str(date), tz="UTC")
        if key not in daily_close.index or np.isnan(daily_close.loc[key]): continue
        if key not in a.index or np.isnan(a.loc[key]): continue
        op = day.iloc[0]["open"]; pc = daily_close.loc[key]
        gap = op - pc
        if abs(gap) < 0.3 * a.loc[key]: continue
        d = -1 if gap > 0 else 1                      # fade the gap
        entries.append((m1.index.get_loc(day.index[0]), d, abs(gap)))
    return m1, entries, 1440

def _from_masks(df, long, short, sl_series):
    long = first_per_day(df, long); short = first_per_day(df, short)
    a = sl_series.values
    entries = []
    for i in np.where(long)[0]:
        if not np.isnan(a[i]): entries.append((i, 1, a[i]))
    for i in np.where(short)[0]:
        if not np.isnan(a[i]): entries.append((i, -1, a[i]))
    return entries


STRATEGIES = {
    "01_orb_break":    ("m1",  lambda d: s_orb_break(d, fade=False)),
    "02_orb_fade":     ("m1",  lambda d: s_orb_break(d, fade=True)),
    "03_firstcandle":  ("m1",  s_firstcandle),
    "04_pdh_pdl_brk":  ("m1",  s_pdh_pdl),
    "05_donchian20":   ("m15", s_donchian),
    "06_rsi2_revert":  ("m15", s_rsi2),
    "07_bb_revert":    ("m15", s_bb_revert),
    "08_ma_pullback":  ("m15", s_ma_pullback),
    "09_vwap_revert":  ("m5",  s_vwap_revert),
    "10_gap_fade":     ("m1",  s_gap_fade),
}


def metrics(t: pd.DataFrame) -> dict:
    if t.empty: return {"trades": 0, "win%": 0, "exp_R": 0, "pf": 0, "total_R": 0, "maxDD_R": 0, "timeout%": 0}
    resolved = t[t["reason"].isin(["TP", "SL"])]
    wr = 100 * (resolved["reason"] == "TP").mean() if len(resolved) else 0.0
    r = t["pnl_R"]; eq = r.cumsum(); dd = (eq - eq.cummax()).min()
    pos, neg = r[r > 0].sum(), r[r < 0].sum()
    return {
        "trades": len(t),
        "win%": round(wr, 1),                          # win-rate on resolved (RR 1:2 races)
        "exp_R": round(r.mean(), 3),
        "pf": round(pos / abs(neg), 2) if neg < 0 else float("inf"),
        "total_R": round(r.sum(), 1),
        "maxDD_R": round(dd, 1),
        "timeout%": round(100 * (t["reason"] == "TIME").mean(), 0),
    }


def main():
    windows = {"IS": ("2020-01-01", "2023-12-31"),
               "OOS": ("2024-01-01", "2026-06-08")}
    all_rows = []
    for sym in SYMBOLS:
        print(f"\n########## {sym} ##########", flush=True)
        m1 = load_m1(sym)
        tfs = {"m1": m1, "m5": resample(m1, "5min"), "m15": resample(m1, "15min")}
        for name, (tf, fn) in STRATEGIES.items():
            base = tfs[tf]
            df_tf, entries, horizon = fn(base)           # entries are RR-independent -> reuse
            print(f"  {sym} {name}: {len(entries)} entries", flush=True)
            for wlabel, (s, e) in windows.items():
                lo = df_tf.index.searchsorted(pd.Timestamp(s, tz="UTC"))
                hi = df_tf.index.searchsorted(pd.Timestamp(e, tz="UTC"))
                sub = [(p, d, sld) for p, d, sld in entries if lo <= p < hi]
                for rr in RR_GRID:
                    m = metrics(simulate(df_tf, sub, horizon, rr))
                    m.update({"symbol": sym, "strategy": name, "tf": tf, "rr": rr, "window": wlabel})
                    all_rows.append(m)
    res = pd.DataFrame(all_rows)
    out = ROOT / "_DOC" / "strategy_lab_rr_sweep.csv"
    res.to_csv(out, index=False)

    # pivot IS vs OOS for the same (symbol,strategy,rr)
    keys = ["symbol", "strategy", "tf", "rr"]
    p = res.pivot_table(index=keys, columns="window",
                        values=["win%", "exp_R", "pf", "trades", "maxDD_R"]).reset_index()
    p.columns = [a if not b else f"{a}_{b}" for a, b in p.columns]
    p["min_win"] = p[["win%_IS", "win%_OOS"]].min(axis=1)
    p["min_exp"] = p[["exp_R_IS", "exp_R_OOS"]].min(axis=1)

    show = ["symbol", "strategy", "rr", "win%_IS", "win%_OOS", "exp_R_IS", "exp_R_OOS",
            "pf_IS", "pf_OOS", "trades_OOS", "maxDD_R_OOS"]

    print("\n\n========= GOAL: WR>50% in BOTH windows AND profitable in BOTH (ranked) =========", flush=True)
    goal = p[(p["min_win"] > 50) & (p["min_exp"] > 0) & (p["trades_OOS"] >= 50)]
    goal = goal.sort_values("min_exp", ascending=False)
    print(goal[show].to_string(index=False) if not goal.empty else "  (none: no config holds WR>50% AND profit in both windows)")

    print("\n\n========= Best WR>50% in BOTH windows (profit or not) =========", flush=True)
    hw = p[(p["min_win"] > 50) & (p["trades_OOS"] >= 50)].sort_values("min_win", ascending=False)
    print(hw[show].head(20).to_string(index=False) if not hw.empty else "  (none reached WR>50% in both windows)")

    print("\n\n========= Best ROBUST edges by min expectancy (any RR) =========", flush=True)
    best = p[(p["min_exp"] > 0) & (p["trades_OOS"] >= 50)].sort_values("min_exp", ascending=False)
    print(best[show].head(20).to_string(index=False) if not best.empty else "  (none)")
    print(f"\nSaved -> {out}", flush=True)


if __name__ == "__main__":
    main()
