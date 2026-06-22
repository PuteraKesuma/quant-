"""Portfolio backtest: ONE common ORB strategy across the FX majors.

Same rule on every pair (the researched winner): London-open 30-min opening range
breakout + range-filter (only trade if range is 0.5-1.5x its 20-day median),
exit RR 1:1 (TP = SL = range), both directions, 1 trade/pair/day.

Portfolio = the brain opens every pair that signals that day (each 0.01 lot). We
report per-pair stats and the combined book: trades, win%, R, drawdown, approx $,
and how many pairs signal per day on average.

    python -m pipeline.analysis.fx_portfolio
"""
import numpy as np
import pandas as pd

from .strategy_lab import load_m1, simulate, ROOT

MAJORS = ["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"]
PIP = {"USDJPY": 0.01}            # default 0.0001 below
UPP = {                          # approx $ per pip at 0.01 lot (micro)
    "EURUSD": 0.10, "GBPUSD": 0.10, "AUDUSD": 0.10, "NZDUSD": 0.10,
    "USDCHF": 0.11, "USDCAD": 0.075, "USDJPY": 0.067,
}
RANGE_MIN = 30
SESSION_OPEN = "08:00"            # London open
RR = 1.0


def orb_range_entries(m1, open_hhmm=SESSION_OPEN, range_min=RANGE_MIN):
    oh, om = map(int, open_hhmm.split(":"))
    # 20-day median of the opening-range size (shifted) for the range filter
    sizes = {}
    for date, day in m1.groupby(m1.index.date):
        st = pd.Timestamp(str(date), tz="UTC").replace(hour=oh, minute=om)
        w = day[(day.index >= st) & (day.index < st + pd.Timedelta(minutes=range_min))]
        sizes[pd.Timestamp(str(date), tz="UTC")] = (w["high"].max() - w["low"].min()) if len(w) >= range_min // 2 else np.nan
    med = pd.Series(sizes).sort_index().rolling(20, min_periods=10).median().shift()

    entries = []
    for date, day in m1.groupby(m1.index.date):
        key = pd.Timestamp(str(date), tz="UTC")
        st = key.replace(hour=oh, minute=om)
        rend = st + pd.Timedelta(minutes=range_min)
        send = key.replace(hour=20)
        win = day[(day.index >= st) & (day.index < rend)]
        if len(win) < range_min // 2:
            continue
        rh, rl = win["high"].max(), win["low"].min(); size = rh - rl
        if size <= 0 or key not in med.index or np.isnan(med.loc[key]):
            continue
        if not (0.5 * med.loc[key] <= size <= 1.5 * med.loc[key]):
            continue
        post = day[(day.index >= rend) & (day.index <= send)]
        for ts, b in post.iterrows():
            d = 1 if b["high"] > rh else (-1 if b["low"] < rl else 0)
            if d:
                entries.append((m1.index.get_loc(ts), d, size))
                break
    return entries


def pair_trades(sym, s, e):
    m1 = load_m1(sym)
    lo = m1.index.searchsorted(pd.Timestamp(s, tz="UTC"))
    hi = m1.index.searchsorted(pd.Timestamp(e, tz="UTC"))
    ent = orb_range_entries(m1)
    sub = [(p, d, sl) for p, d, sl in ent if lo <= p < hi]
    slb = {p: sl for p, d, sl in sub}
    r = simulate(m1, sub, 1440, RR)
    if r.empty:
        return r
    r["ts"] = m1.index[r["pos"].values]
    r["R"] = r["pos"].map(slb)
    pip = PIP.get(sym, 0.0001)
    r["usd"] = r["pnl_R"] * r["R"] / pip * UPP[sym]      # $ at 0.01 lot
    r["sym"] = sym
    return r[["ts", "sym", "pnl_R", "usd", "reason"]]


def stats(r, label, months):
    if r.empty:
        return {"book": label, "trades": 0}
    pr = r["pnl_R"]; eq = pr.cumsum(); dd = (eq - eq.cummax()).min()
    ueq = r.sort_values("ts")["usd"].cumsum(); udd = (ueq - ueq.cummax()).min()
    pos, neg = pr[pr > 0].sum(), pr[pr < 0].sum()
    return {
        "book": label, "trades": len(r),
        "win%": round(100 * (pr > 0).mean(), 1),
        "PF": round(pos / abs(neg), 2) if neg < 0 else float("inf"),
        "total_R": round(pr.sum(), 1), "maxDD_R": round(dd, 1),
        "$/mo@0.01": round(r["usd"].sum() / months, 1),
        "maxDD_$": round(udd, 0),
    }


def main():
    windows = {"IS": ("2020-01-01", "2023-12-31", 48.0),
               "OOS": ("2024-01-01", "2026-06-10", 29.5)}
    for wlabel, (s, e, mo) in windows.items():
        print(f"\n================= {wlabel} {s}..{e} =================")
        all_tr = []
        rows = []
        for sym in MAJORS:
            try:
                r = pair_trades(sym, s, e)
            except Exception as ex:
                print(f"  {sym}: skip ({ex})"); continue
            if r.empty:
                continue
            all_tr.append(r)
            rows.append(stats(r, sym, mo))
        print(pd.DataFrame(rows).to_string(index=False))
        if not all_tr:
            continue
        book = pd.concat(all_tr).sort_values("ts")
        print("\n  --- PORTFOLIO (open every pair that signals, 0.01 lot each) ---")
        print("  " + pd.DataFrame([stats(book, "ALL MAJORS", mo)]).to_string(index=False).replace("\n", "\n  "))
        per_day = book.groupby(book["ts"].dt.normalize()).size()
        print(f"  trading days: {len(per_day)} | avg pairs/day: {per_day.mean():.1f} | max pairs/day: {per_day.max()}")
        print(f"  portfolio $/day (avg): ${book['usd'].sum()/ (mo*21):.2f}")


if __name__ == "__main__":
    main()
