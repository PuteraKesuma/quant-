"""ORB strategy research sweep for a single symbol.

Scans Opening Range Breakout variants over historical 1m data and ranks them.
Supports both classic TP/SL exits and the time-exit (no price SL) style used live.

Metrics are normalised in **R = the opening-range size** (the natural risk unit
for ORB), so configs with different range lengths are comparable. Also reports
PnL in raw price points (for XAU at 0.01 lot, 1 price point ~= $1).

    python -m pipeline.analysis.orb_research --symbol XAUUSD
"""
import argparse
import itertools
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent.parent


def load_ohlcv(symbol: str) -> pd.DataFrame:
    db = ROOT / "data" / "Level_0_Raw" / f"{symbol}_1m.duckdb"
    con = duckdb.connect(str(db), read_only=True)
    con.execute("SET TimeZone='UTC'")
    cols = [r[1] for r in con.execute("PRAGMA table_info('ohlcv')").fetchall()]
    where = "WHERE NOT is_synthetic" if "is_synthetic" in cols else ""
    df = con.execute(f"SELECT ts,open,high,low,close FROM ohlcv {where} ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def backtest(df, open_hhmm, range_min, tp_mult, use_sl, sl_mult, end_hhmm):
    oh, om = map(int, open_hhmm.split(":"))
    eh, em = map(int, end_hhmm.split(":"))
    rows = []
    for date, day in df.groupby(df.index.date):
        start = pd.Timestamp(str(date), tz="UTC").replace(hour=oh, minute=om)
        rend = start + pd.Timedelta(minutes=range_min)
        send = pd.Timestamp(str(date), tz="UTC").replace(hour=eh, minute=em)

        win = day[(day.index >= start) & (day.index < rend)]
        if len(win) < range_min // 2:
            continue
        rh = win["high"].max()
        rl = win["low"].min()
        size = rh - rl
        if size <= 0:
            continue

        post = day[(day.index >= rend) & (day.index <= send)]
        if post.empty:
            continue

        # --- find first breakout ---
        direction = entry = entry_i = None
        H = post["high"].values; L = post["low"].values
        for i in range(len(post)):
            up = H[i] > rh
            dn = L[i] < rl
            if up and dn:                        # bar breaches both -> use close vs open
                direction = "long" if post["close"].values[i] >= post["open"].values[i] else "short"
            elif up:
                direction = "long"
            elif dn:
                direction = "short"
            else:
                continue
            entry = rh if direction == "long" else rl
            entry_i = i
            break
        if entry_i is None:
            continue

        tp = sl = None
        if direction == "long":
            if tp_mult: tp = entry + tp_mult * size
            if use_sl:  sl = entry - sl_mult * size
        else:
            if tp_mult: tp = entry - tp_mult * size
            if use_sl:  sl = entry + sl_mult * size

        # --- simulate exit from the breakout bar onward (SL checked before TP) ---
        exit_price = post["close"].values[-1]    # default = time exit at session end
        reason = "TIME"
        for i in range(entry_i, len(post)):
            hi, lo = H[i], L[i]
            if direction == "long":
                if sl is not None and lo <= sl: exit_price, reason = sl, "SL"; break
                if tp is not None and hi >= tp: exit_price, reason = tp, "TP"; break
            else:
                if sl is not None and hi >= sl: exit_price, reason = sl, "SL"; break
                if tp is not None and lo <= tp: exit_price, reason = tp, "TP"; break

        pnl_price = (exit_price - entry) if direction == "long" else (entry - exit_price)
        rows.append((date, direction, size, pnl_price, pnl_price / size, reason))

    return pd.DataFrame(rows, columns=["date", "dir", "range", "pnl_price", "pnl_R", "reason"])


def metrics(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"trades": 0}
    r = t["pnl_R"]
    eq = r.cumsum()
    dd = (eq - eq.cummax()).min()
    pos, neg = r[r > 0].sum(), r[r < 0].sum()
    return {
        "trades": len(r),
        "win%": round(100 * (r > 0).mean(), 1),
        "total_R": round(r.sum(), 1),
        "exp_R": round(r.mean(), 3),
        "pf": round(pos / abs(neg), 2) if neg < 0 else float("inf"),
        "maxDD_R": round(dd, 1),
        "tot_price": round(t["pnl_price"].sum(), 0),
        "tp%": round(100 * (t["reason"] == "TP").mean(), 0),
    }


def sweep(df, sessions, ranges, exit_cfgs, end_hhmm, tag):
    out = []
    for (sname, sopen), rmin, (ename, use_sl, sl_mult, tp_mult) in itertools.product(
            sessions.items(), ranges, exit_cfgs):
        t = backtest(df, sopen, rmin, tp_mult, use_sl, sl_mult, end_hhmm)
        m = metrics(t)
        m.update({"session": sname, "range": rmin, "exit": ename, "window": tag})
        out.append(m)
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="XAUUSD")
    ap.add_argument("--end_hhmm", default="20:00")
    args = ap.parse_args()

    df = load_ohlcv(args.symbol)
    print(f"Loaded {len(df):,} bars {df.index.min()} -> {df.index.max()}")

    sessions = {"london": "08:00", "new_york": "13:30"}
    ranges = [15, 30, 60]
    exit_cfgs = [
        # name,             use_sl, sl_mult, tp_mult
        ("SL1_TP1",         True,  1.0, 1.0),
        ("SL1_TP2",         True,  1.0, 2.0),
        ("SL1_TP3",         True,  1.0, 3.0),
        ("time_TP2",        False, 0.0, 2.0),   # TP at 2x range else time-exit
        ("time_TP3",        False, 0.0, 3.0),
        ("time_noTP",       False, 0.0, None),  # pure time-exit (no SL, no TP)
    ]

    cols = ["session", "range", "exit", "trades", "win%", "total_R",
            "exp_R", "pf", "maxDD_R", "tot_price", "tp%"]

    windows = {"IS 2020-2023": ("2020-01-01", "2023-12-31"),
               "OOS 2024-2026": ("2024-01-01", "2026-06-08"),
               "ALL 2020-2026": ("2020-01-01", "2026-06-08")}

    results = {}
    for tag, (s, e) in windows.items():
        res = sweep(df[s:e], sessions, ranges, exit_cfgs, args.end_hhmm, tag)
        results[tag] = res
        print(f"\n===== {tag} (ranked by total_R) =====")
        print(res.sort_values("total_R", ascending=False)[cols].to_string(index=False))

    # save full grid
    out = ROOT / "_DOC" / "xau_orb_research.csv"
    pd.concat(results.values()).to_csv(out, index=False)
    print(f"\nSaved full grid -> {out}")


if __name__ == "__main__":
    main()
