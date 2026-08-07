"""ETERNA fase-2: sapu parameter + walk-forward IS/OOS.

Baseline (eterna_baseline.py) menunjukkan sinyal EternaBot hanya hidup di TF besar
(M30/H1/H4) dan MATI di M1/M5 (setelan bawaan EA = rugi telak). Fase ini menjawab:
apakah itu PLATEAU yang nyata, atau SPIKE kebetulan?

Prinsip (pelajaran Golden 2026-07-17):
- Plateau yang lebar & monoton = edge nyata. Spike sendirian di tengah padang = overfit.
- Wajib punya OOS. IS = 2021..2024, OOS = 2025..2026-06.
- Syarat sampel minimum n>=200 (IS) dan n>=60 (OOS); di bawah itu tidak dinilai.

Jalankan: python research/eterna_sweep.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
SL_POINTS, POINT, LOT, CONTRACT, COST = 1000, 0.01, 0.03, 100.0, 0.35
OOS_START = pd.Timestamp("2025-01-01", tz="UTC")

TFS = {"M30": "30min", "H1": "1h", "H4": "4h"}
ATR_PERIODS = [7, 10, 14, 20]
MULT_ENTRY = [1.2, 1.8, 2.5, 3.2]
MULT_TREND = 3.8
SESSIONS = {
    "all": None,
    "asia": (23, 30, 4, 0),
    "london": (5, 30, 10, 0),
    "ny": (11, 30, 16, 0),
}


def load_1m():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def resample(df1m, tf):
    return df1m.resample(tf, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def atr(df, n):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def supertrend(df, period, mult):
    a = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2.0
    upper = (hl2 + mult * a).to_numpy()
    lower = (hl2 - mult * a).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    fu = np.full(n, np.nan); fl = np.full(n, np.nan); dirn = np.ones(n, dtype=int)
    for i in range(1, n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        fu[i] = upper[i] if (np.isnan(fu[i-1]) or upper[i] < fu[i-1] or close[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = lower[i] if (np.isnan(fl[i-1]) or lower[i] > fl[i-1] or close[i-1] < fl[i-1]) else fl[i-1]
        if not np.isnan(fu[i-1]) and close[i] > fu[i]:
            dirn[i] = 1
        elif not np.isnan(fl[i-1]) and close[i] < fl[i]:
            dirn[i] = -1
        else:
            dirn[i] = dirn[i-1]
    return pd.Series(dirn, index=df.index)


def in_session(idx, sess):
    if sess is None:
        return np.ones(len(idx), dtype=bool)
    sh, sm, eh, em = sess
    mins = idx.hour * 60 + idx.minute
    s, e = sh * 60 + sm, eh * 60 + em
    return (mins >= s) & (mins < e) if s < e else (mins >= s) | (mins < e)


def run(df, st_e, st_t, sess, use_filter):
    flip = (st_e != st_e.shift(1))
    sd = st_e.where(flip).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    ok = in_session(df.index, sess)
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    sl_dist = SL_POINTS * POINT
    pos, entry, entry_i, tr = 0, 0.0, 0, []

    for i in range(1, len(df)):
        if pos != 0:
            if pos == 1 and l[i] <= entry - sl_dist:
                tr.append((df.index[entry_i], pos, entry, entry - sl_dist)); pos = 0
            elif pos == -1 and h[i] >= entry + sl_dist:
                tr.append((df.index[entry_i], pos, entry, entry + sl_dist)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if use_filter and not np.isnan(td[i]) and int(td[i]) != s:
            if pos == -s:
                tr.append((df.index[entry_i], pos, entry, o[i])); pos = 0
            continue
        if not ok[i]:
            continue
        if pos == 0:
            pos, entry, entry_i = s, o[i], i
        elif pos != s:
            tr.append((df.index[entry_i], pos, entry, o[i]))
            pos, entry, entry_i = s, o[i], i

    if not tr:
        return None
    t = pd.DataFrame(tr, columns=["t_in", "dir", "px_in", "px_out"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    t["R"] = t.pnl / (sl_dist * LOT * CONTRACT)
    return t


def stats(t, label):
    if t is None or len(t) == 0:
        return {f"n_{label}": 0}
    w, l = t.loc[t.pnl > 0, "pnl"], t.loc[t.pnl <= 0, "pnl"]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    eq = t.pnl.cumsum()
    return {f"n_{label}": len(t), f"PF_{label}": round(pf, 2),
            f"avgR_{label}": round(t.R.mean(), 3), f"net_{label}": round(t.pnl.sum(), 0),
            f"DD_{label}": round((eq - eq.cummax()).min(), 0)}


def main():
    print("Memuat XAUUSD 1m ...")
    df1m = load_1m()
    rows = []

    for tf_label, tf in TFS.items():
        d = resample(df1m, tf)
        print(f"\n=== {tf_label}  ({len(d):,} bar) ===")
        st_trend = {p: supertrend(d, p, MULT_TREND) for p in ATR_PERIODS}
        for p in ATR_PERIODS:
            for me in MULT_ENTRY:
                st_e = supertrend(d, p, me)
                for sess_name, sess in SESSIONS.items():
                    for mode, uf in [("direct", False), ("conservative", True)]:
                        t = run(d, st_e, st_trend[p], sess, uf)
                        if t is None or len(t) < 200:
                            continue
                        is_t = t[t.t_in < OOS_START]
                        oos_t = t[t.t_in >= OOS_START]
                        if len(is_t) < 200 or len(oos_t) < 60:
                            continue
                        yearly = t.groupby(t.t_in.dt.year).pnl.sum()
                        row = {"TF": tf_label, "atr": p, "mult_e": me,
                               "sesi": sess_name, "mode": mode,
                               "hijau": f"{int((yearly>0).sum())}/{len(yearly)}"}
                        row.update(stats(is_t, "IS"))
                        row.update(stats(oos_t, "OOS"))
                        rows.append(row)
        print(f"  konfigurasi lolos syarat sampel sejauh ini: {len(rows)}")

    out = pd.DataFrame(rows)
    if out.empty:
        print("\nTIDAK ADA konfigurasi yang lolos syarat sampel minimum.")
        return
    out = out.sort_values("avgR_OOS", ascending=False)
    out.to_csv(r"C:\Quant\_MONITOR\eterna_sweep.csv", index=False)

    print("\n" + "=" * 118)
    print("TOP 20 berdasarkan avgR OUT-OF-SAMPLE (2025-2026):")
    cols = ["TF", "atr", "mult_e", "sesi", "mode", "n_IS", "PF_IS", "avgR_IS",
            "n_OOS", "PF_OOS", "avgR_OOS", "net_OOS", "DD_OOS", "hijau"]
    print(out[cols].head(20).to_string(index=False))

    pos_both = out[(out.avgR_IS > 0) & (out.avgR_OOS > 0)]
    print(f"\nTotal konfigurasi dinilai : {len(out)}")
    print(f"Positif di IS DAN OOS     : {len(pos_both)}  ({100*len(pos_both)/len(out):.0f}%)")
    print("\nKalau persentase ini rendah, sinyalnya rapuh (spike). Kalau tinggi & tersebar")
    print("di banyak parameter bertetangga, itu tanda plateau yang nyata.")
    print("\nDisimpan: C:\\Quant\\_MONITOR\\eterna_sweep.csv")


if __name__ == "__main__":
    main()
