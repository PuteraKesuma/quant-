"""ETERNA fase-13: VERIFIKASI bentuk LIVE (voting), bukan portofolio.

Masalah kesetiaan: fase 1-12 memodelkan ensemble sebagai 32 POSISI PARALEL, masing-masing
1/32 ukuran. Live tidak bisa begitu — lot minimum 0.01 dan satu slot pegang satu posisi.
Bentuk live yang realistis adalah VOTING: 32 anggota memberi suara arah, mayoritas menang,
satu posisi dibuka dengan SL/TP = MEDIAN dari anggota yang setuju.

Voting != portofolio. Skrip ini menguji apakah voting berperilaku setara. Kalau jauh lebih
buruk, yang di-deploy TIDAK boleh mengklaim angka validasi portofolio.

Anggota = 32: ATR{7,10,14,20} x entry{1.8,2.5} x tren{3.8,5.0} x TP{3,4}, mode konservatif.

Jalankan: python research/eterna_voting_live.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.01, 100.0, 0.50
CAPITAL, MIN_SL_DIST, STRUCT = 1000.0, 0.50, 20
SIDEWAYS = (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"))

ATRS, MULT_E, MULT_T, TPS = [7, 10, 14, 20], [1.8, 2.5], [3.8, 5.0], [3.0, 4.0]
MEMBERS = [(p, me, mt, tp) for p in ATRS for me in MULT_E for mt in MULT_T for tp in TPS]


def load_h1():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts").resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def atr_s(df, n):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def supertrend(df, period, mult):
    a = atr_s(df, period)
    hl2 = (df["high"] + df["low"]) / 2.0
    up = (hl2 + mult * a).to_numpy(); lo = (hl2 - mult * a).to_numpy()
    c = df["close"].to_numpy(); n = len(df)
    fu = np.full(n, np.nan); fl = np.full(n, np.nan); d = np.ones(n, dtype=int)
    for i in range(1, n):
        if np.isnan(up[i]) or np.isnan(lo[i]):
            continue
        fu[i] = up[i] if (np.isnan(fu[i-1]) or up[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = lo[i] if (np.isnan(fl[i-1]) or lo[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]
        if not np.isnan(fu[i-1]) and c[i] > fu[i]:
            d[i] = 1
        elif not np.isnan(fl[i-1]) and c[i] < fl[i]:
            d[i] = -1
        else:
            d[i] = d[i-1]
    return pd.Series(d, index=df.index)


def member_states(df, sts):
    """Untuk tiap anggota, hasilkan array arah posisi per bar (+1/-1/0) — simulasi mandiri."""
    n = len(df)
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    slo = df["low"].rolling(STRUCT).min().shift(1).to_numpy()
    shi = df["high"].rolling(STRUCT).max().shift(1).to_numpy()
    states, port = [], []
    for (p, me, mt, tpr) in MEMBERS:
        sd = sts[(p, me)].where(sts[(p, me)] != sts[(p, me)].shift(1)).shift(1).to_numpy()
        td = sts[(p, mt)].shift(1).to_numpy()
        st = np.zeros(n, dtype=np.int8)
        pos = 0; entry = sl = tp = 0.0; ei = 0; trades = []
        for i in range(1, n):
            if pos != 0:
                hit = None
                if pos == 1:
                    hit = sl if l[i] <= sl else (tp if h[i] >= tp else None)
                else:
                    hit = sl if h[i] >= sl else (tp if l[i] <= tp else None)
                if hit is not None:
                    trades.append((df.index[ei], pos, entry, hit)); pos = 0
            s = sd[i]
            if not np.isnan(s):
                s = int(s)
                if pos == -s:
                    trades.append((df.index[ei], pos, entry, o[i])); pos = 0
                if pos == 0 and not np.isnan(td[i]) and int(td[i]) == s:
                    raw = slo[i] if s == 1 else shi[i]
                    if not np.isnan(raw):
                        dist = abs(o[i] - raw)
                        if dist >= MIN_SL_DIST:
                            pos, entry, ei = s, o[i], i
                            sl = o[i] - dist if s == 1 else o[i] + dist
                            tp = o[i] + tpr * dist if s == 1 else o[i] - tpr * dist
            st[i] = pos
        states.append(st)
        t = pd.DataFrame(trades, columns=["t_in", "dir", "px_in", "px_out"])
        t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
        port.append(t.set_index("t_in")[["pnl"]] / len(MEMBERS))
    return np.array(states), pd.concat(port).sort_index()


def vote_backtest(df, states, thresh):
    """Satu posisi. Arah = mayoritas suara anggota; SL/TP = median anggota yang setuju."""
    n = len(df)
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    slo = df["low"].rolling(STRUCT).min().shift(1).to_numpy()
    shi = df["high"].rolling(STRUCT).max().shift(1).to_numpy()
    tps = np.array([m[3] for m in MEMBERS])
    longs = (states == 1).sum(axis=0)
    shorts = (states == -1).sum(axis=0)
    total = len(MEMBERS)

    pos = 0; entry = sl = tp = 0.0; ei = 0; out = []
    for i in range(1, n):
        if pos != 0:
            hit = None
            if pos == 1:
                hit = sl if l[i] <= sl else (tp if h[i] >= tp else None)
            else:
                hit = sl if h[i] >= sl else (tp if l[i] <= tp else None)
            if hit is not None:
                out.append((df.index[ei], pos, entry, hit)); pos = 0

        net = longs[i] - shorts[i]
        want = 1 if net >= thresh * total else (-1 if net <= -thresh * total else 0)

        if pos != 0 and want != pos:
            out.append((df.index[ei], pos, entry, o[i])); pos = 0
        if pos == 0 and want != 0:
            raw = slo[i] if want == 1 else shi[i]
            if np.isnan(raw):
                continue
            dist = abs(o[i] - raw)
            if dist < MIN_SL_DIST:
                continue
            agree = states[:, i] == want
            tpr = float(np.median(tps[agree])) if agree.any() else 3.5
            pos, entry, ei = want, o[i], i
            sl = o[i] - dist if want == 1 else o[i] + dist
            tp = o[i] + tpr * dist if want == 1 else o[i] - tpr * dist
    if not out:
        return None
    t = pd.DataFrame(out, columns=["t_in", "dir", "px_in", "px_out"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    return t.set_index("t_in")[["pnl"]]


def stats(c, label):
    if c is None or len(c) < 30:
        return None
    eq = c.pnl.cumsum()
    dd = (eq - eq.cummax()).min()
    w, l = c.loc[c.pnl > 0, "pnl"], c.loc[c.pnl <= 0, "pnl"]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    yr = c.groupby(c.index.year).pnl.sum()
    m = c.pnl.resample("ME").sum(); m = m[m != 0]
    streak = mx = 0
    for v in m:
        streak = streak + 1 if v < 0 else 0
        mx = max(mx, streak)
    return {"varian": label, "n": len(c), "net": round(c.pnl.sum()), "PF": round(pf, 2),
            "maxDD": round(dd), "RetDD": round((c.pnl.sum() / 5.5) / abs(dd), 2) if dd else np.nan,
            "hijau": f"{int((yr>0).sum())}/{len(yr)}", "merah%": round(100 * (m < 0).mean()),
            "beruntun": mx, "netSide": round(c.loc[SIDEWAYS[0]:SIDEWAYS[1]].pnl.sum())}


def main():
    d = load_h1()
    sts = {}
    for p in ATRS:
        for m in set(MULT_E + MULT_T):
            sts[(p, m)] = supertrend(d, p, m)
    print(f"H1 {len(d):,} bar | {len(MEMBERS)} anggota | biaya ${COST}\n")
    print("Mensimulasikan tiap anggota ...", flush=True)
    states, port = member_states(d, sts)

    rows = [stats(port, "PORTOFOLIO 32 paralel (yang divalidasi)")]
    for th in (0.15, 0.25, 0.35, 0.50):
        rows.append(stats(vote_backtest(d, states, th), f"VOTING ambang {th:.0%} suara bersih"))
    rows = [r for r in rows if r]
    df = pd.DataFrame(rows)
    print("\n" + "=" * 122)
    print("PORTOFOLIO vs VOTING")
    print("=" * 122)
    print(df.to_string(index=False))

    base = df.iloc[0]
    print("\n" + "=" * 122)
    best = df.iloc[1:].sort_values("RetDD", ascending=False).iloc[0]
    print(f"Voting terbaik : {best['varian']}")
    print(f"  net ${best['net']} vs portofolio ${base['net']}  "
          f"({100*(best['net']-base['net'])/abs(base['net']):+.0f}%)")
    print(f"  Ret/DD {best['RetDD']} vs {base['RetDD']}")
    print(f"  sideways ${best['netSide']} vs ${base['netSide']}")
    ok = best["RetDD"] >= base["RetDD"] * 0.80 and best["netSide"] > 0
    print(f"\n  >> {'SETARA — voting boleh di-deploy' if ok else 'TIDAK SETARA — jangan klaim angka portofolio'}")
    df.to_csv(r"C:\Quant\_MONITOR\eterna_voting_live.csv", index=False)


if __name__ == "__main__":
    main()
