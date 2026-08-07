"""ETERNA fase-17: GATE MULTI-TIMEFRAME + DD per anggota + sesi (dengan struct terkoreksi).

User menantang: DD masih 16,2%, seharusnya bisa 10-12%. Yang belum diuji SECARA KONSEP:

1. GATE DARI TIMEFRAME LEBIH TINGGI.
   Selama ini kedua Supertrend dihitung di TF yang sama (H1), karena begitu cara EA
   (keduanya pakai `_Period`). TAPI semua strategi user yang bertahan memakai gate TF
   lebih tinggi: zrev_xau = H1 EMA100 + Daily SMA50; Golden = gate H1 untuk entry M5.
   Petunjuk kuat: kelemahan eterna ada di regime DATAR (netSide cuma +$148). Gate lebih
   lambat menahan sistem keluar dari pasar choppy — dan di situlah drawdown lahir.

2. DD PER ANGGOTA (selama ini cuma DD ensemble yang dilaporkan).

3. SESI — fase-8 menguji sesi dengan struct=20 yang SALAH; belum diulang.

4. Gate lebih ketat (mult_tren 6.0, 7.0).

!!! ANTI-LOOKAHEAD !!!
Gate MTF adalah PERSIS mekanisme yang membunuh Golden (reindex H1->M5 dgn ffill TANPA
shift -> lookahead 55 menit). Di sini: Supertrend dihitung di frame TF-tinggi, di-`shift(1)`
DULU (sehingga hanya memakai bar TF-tinggi yang SUDAH TERTUTUP), baru di-reindex ke H1
dengan ffill. Ada uji lookahead eksplisit di akhir skrip.

Jalankan: python research/eterna_mtf_gate.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.01, 100.0, 0.50
CAPITAL, MIN_SL = 1000.0, 0.30
SIDEWAYS = (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"))

PERIODS = [7, 10, 14, 20]
MULT_E = 1.8
TP_R = 4.0
GATE_TFS = {"H1(sama)": None, "H4": "4h", "D1": "1D"}
GATE_MULTS = [3.8, 5.0, 6.0, 7.0]
SESSIONS = {"all": None, "asia": (23, 30, 4, 0), "london": (5, 30, 10, 0), "ny": (11, 30, 16, 0)}


def load_1m():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def rs(df, tf):
    return df.resample(tf, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def atr_s(df, n):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
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


def gate_series(df1m, h1_index, gate_tf, period, mult):
    """Gate dari TF lebih tinggi, BEBAS LOOKAHEAD.

    Urutan WAJIB: hitung Supertrend di frame TF-tinggi -> shift(1) (pakai hanya bar yang
    SUDAH tertutup) -> baru reindex ke H1 dengan ffill. Membalik urutan ini = bug Golden.
    """
    if gate_tf is None:
        return None
    g = rs(df1m, gate_tf)
    st = supertrend(g, period, mult)
    st = st.shift(1)                                   # <-- kunci anti-lookahead
    return st.reindex(h1_index, method="ffill")


def run(h, st_e, gate, struct_bars, sess):
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = gate.to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(struct_bars).min().shift(1).to_numpy()
    shi = h["high"].rolling(struct_bars).max().shift(1).to_numpy()
    if sess is None:
        ok = np.ones(len(h), dtype=bool)
    else:
        sh, sm, eh, em = sess
        mins = h.index.hour * 60 + h.index.minute
        s0, e0 = sh * 60 + sm, eh * 60 + em
        ok = ((mins >= s0) & (mins < e0)) if s0 < e0 else ((mins >= s0) | (mins < e0))
    pos = 0; entry = sl = tp = risk = 0.0; ei = 0; out = []
    for i in range(1, len(h)):
        if pos != 0:
            hit = None
            if pos == 1:
                hit = sl if lo[i] <= sl else (tp if hi[i] >= tp else None)
            else:
                hit = sl if hi[i] >= sl else (tp if lo[i] <= tp else None)
            if hit is not None:
                out.append((h.index[ei], pos, entry, hit, risk)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((h.index[ei], pos, entry, o[i], risk)); pos = 0
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s or not ok[i]:
            continue
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(o[i] - raw)
        if dist < MIN_SL:
            continue
        pos, entry, ei, risk = s, o[i], i, dist
        sl = o[i] - dist if s == 1 else o[i] + dist
        tp = o[i] + TP_R * dist if s == 1 else o[i] - TP_R * dist
    if not out:
        return None
    t = pd.DataFrame(out, columns=["t_in", "dir", "px_in", "px_out", "risk"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    return t.set_index("t_in")


def summarize(c, label, div=1):
    if c is None or len(c) < 80:
        return None
    pnl = c.pnl / div
    eq = CAPITAL + pnl.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    w, l = pnl[pnl > 0], pnl[pnl <= 0]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    yr = pnl.groupby(c.index.year).sum()
    m = pnl.resample("ME").sum(); m = m[m != 0]
    streak = mx = 0
    for v in m:
        streak = streak + 1 if v < 0 else 0
        mx = max(mx, streak)
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    thn = 100 * (pnl.sum() / yrs) / CAPITAL
    return {"konfigurasi": label, "n": len(c), "net": round(pnl.sum()), "PF": round(pf, 2),
            "maxDD%": round(dd, 1), "thn%": round(thn, 1),
            "Ret/DD": round(thn / abs(dd), 2) if dd else np.nan,
            "hijau": f"{int((yr>0).sum())}/{len(yr)}", "merah%": round(100*(m<0).mean()),
            "beruntun": mx, "netSide": round(pnl.loc[SIDEWAYS[0]:SIDEWAYS[1]].sum())}


def main():
    df1m = load_1m()
    h = rs(df1m, "1h")
    print(f"H1 {len(h):,} bar\n")
    st_e = {p: supertrend(h, p, MULT_E) for p in PERIODS}

    rows = []
    print("A. GATE dari berbagai timeframe (ensemble 4 periode, sesi all)", flush=True)
    for gname, gtf in GATE_TFS.items():
        for gm in GATE_MULTS:
            frames = []
            for p in PERIODS:
                g = (supertrend(h, p, gm).shift(1) if gtf is None
                     else gate_series(df1m, h.index, gtf, p, gm))
                frames.append(run(h, st_e[p], g, p, None))
            frames = [f for f in frames if f is not None]
            if not frames:
                continue
            c = pd.concat(frames).sort_index()
            s = summarize(c, f"gate {gname:8} x{gm}", div=len(frames))
            if s:
                rows.append(s)
    df = pd.DataFrame(rows).sort_values("Ret/DD", ascending=False)
    print(df.to_string(index=False))

    best = df.iloc[0]["konfigurasi"]
    gname = best.split()[1]
    gm = float(best.split("x")[-1])
    gtf = GATE_TFS[[k for k in GATE_TFS if k.startswith(gname)][0]]
    print(f"\nGate terbaik: {best}\n")

    print("=" * 118)
    print("B. DD PER ANGGOTA (gate terbaik) — apakah ada anggota tunggal ber-DD 10-12%?")
    print("=" * 118)
    mrows = []
    for p in PERIODS:
        g = (supertrend(h, p, gm).shift(1) if gtf is None
             else gate_series(df1m, h.index, gtf, p, gm))
        t = run(h, st_e[p], g, p, None)
        s = summarize(t, f"anggota ATR {p}")
        if s:
            mrows.append(s)
    print(pd.DataFrame(mrows).to_string(index=False))

    print("\n" + "=" * 118)
    print("C. SESI (gate terbaik, struct terkoreksi) — fase-8 memakai struct 20 yg salah")
    print("=" * 118)
    srows = []
    for sname, sess in SESSIONS.items():
        frames = []
        for p in PERIODS:
            g = (supertrend(h, p, gm).shift(1) if gtf is None
                 else gate_series(df1m, h.index, gtf, p, gm))
            frames.append(run(h, st_e[p], g, p, sess))
        frames = [f for f in frames if f is not None]
        if not frames:
            continue
        c = pd.concat(frames).sort_index()
        s = summarize(c, f"sesi {sname}", div=len(frames))
        if s:
            srows.append(s)
    print(pd.DataFrame(srows).sort_values("Ret/DD", ascending=False).to_string(index=False))

    print("\n" + "=" * 118)
    print("D. UJI LOOKAHEAD pada gate MTF (wajib — ini yang membunuh Golden)")
    print("=" * 118)
    g4 = gate_series(df1m, h.index, "4h", 10, 5.0)
    h4 = rs(df1m, "4h")
    st4 = supertrend(h4, 10, 5.0)
    bad = 0
    for ts in h.index[5000:5200]:
        val = g4.loc[ts]
        if pd.isna(val):
            continue
        # nilai gate pada jam ts HARUS berasal dari bar H4 yang sudah TERTUTUP sebelum ts
        closed = st4.loc[st4.index + pd.Timedelta("4h") <= ts]
        if len(closed) and val != closed.iloc[-1]:
            bad += 1
    print(f"  bar diperiksa 200, memakai bar H4 yang BELUM tertutup: {bad}")
    print("  " + ("LOLOS — gate hanya memakai bar tertutup." if bad == 0
                  else "*** GAGAL: ADA LOOKAHEAD — hasil di atas TIDAK SAH ***"))

    pd.DataFrame(rows).to_csv(r"C:\Quant\_MONITOR\eterna_mtf_gate.csv", index=False)


if __name__ == "__main__":
    main()
