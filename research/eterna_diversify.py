"""ETERNA fase-18: DIVERSIFIKASI — sleeve cepat M30 (SL/TP pendek) + sleeve lambat H1.

Ide user: "diversifikasi dengan timeframe 30, SL cepat dan TP cepat, biar DD terkontrol."
Ini pendekatan yang BENAR untuk menurunkan DD — bukan tuning parameter, tapi menggabungkan
dua profil payoff yang berbeda:

  SLEEVE LAMBAT (H1)  : TP 1:4, menang JARANG tapi BESAR (WR ~38%), naik saat tren panjang.
  SLEEVE CEPAT (M30)  : TP 1:1..1:2, struct pendek, menang SERING tapi KECIL (WR tinggi),
                        panen di ayunan pendek — termasuk saat pasar tidak tren panjang.

Kalau korelasi bulanannya rendah, DD gabungan turun LEBIH BANYAK daripada return-nya
(itu inti diversifikasi). Kalau korelasinya tinggi, gabungan tidak menolong dan kita
tinggal memilih yang terbaik.

Temuan fase-17 yang dipakai: ensemble ATR {7,10,14,20} justru LEBIH BURUK dari ATR 20
tunggal (anggota terlalu berkorelasi). Jadi sleeve lambat di sini = ATR 14 & 20 saja.

Jalankan: python research/eterna_diversify.py
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


def run(h, st_e, st_t, struct_bars, tp_r):
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(struct_bars).min().shift(1).to_numpy()
    shi = h["high"].rolling(struct_bars).max().shift(1).to_numpy()
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
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s:
            continue
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(o[i] - raw)
        if dist < MIN_SL:
            continue
        pos, entry, ei, risk = s, o[i], i, dist
        sl = o[i] - dist if s == 1 else o[i] + dist
        tp = o[i] + tp_r * dist if s == 1 else o[i] - tp_r * dist
    if not out:
        return None
    t = pd.DataFrame(out, columns=["t_in", "dir", "px_in", "px_out", "risk"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    return t.set_index("t_in")[["pnl", "risk"]]


def stat(pnl, label):
    if pnl is None or len(pnl) < 60:
        return None
    eq = CAPITAL + pnl.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    w, l = pnl[pnl > 0], pnl[pnl <= 0]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    yr = pnl.groupby(pnl.index.year).sum()
    m = pnl.resample("ME").sum(); m = m[m != 0]
    streak = mx = 0
    for v in m:
        streak = streak + 1 if v < 0 else 0
        mx = max(mx, streak)
    yrs = (pnl.index[-1] - pnl.index[0]).days / 365.25
    thn = 100 * (pnl.sum() / yrs) / CAPITAL
    return {"sleeve": label, "n": len(pnl), "net": round(pnl.sum()), "PF": round(pf, 2),
            "WR%": round(100 * (pnl > 0).mean()), "maxDD%": round(dd, 1),
            "thn%": round(thn, 1), "Ret/DD": round(thn / abs(dd), 2) if dd else np.nan,
            "hijau": f"{int((yr>0).sum())}/{len(yr)}", "merah%": round(100*(m<0).mean()),
            "beruntun": mx, "netSide": round(pnl.loc[SIDEWAYS[0]:SIDEWAYS[1]].sum())}


def main():
    df1m = load_1m()
    h1, m30 = rs(df1m, "1h"), rs(df1m, "30min")
    print(f"H1 {len(h1):,} bar | M30 {len(m30):,} bar\n")

    # ---------- SLEEVE LAMBAT: H1, TP 1:4, ATR 14 & 20 (fase-17: yang terbaik) ----------
    slow = []
    for p in (14, 20):
        t = run(h1, supertrend(h1, p, 1.8), supertrend(h1, p, 3.8), p, 4.0)
        if t is not None:
            slow.append(t.pnl / 2)
    slow_pnl = pd.concat(slow).sort_index()

    print("=" * 116)
    print("A. SLEEVE CEPAT M30 — cari SL pendek + TP pendek terbaik")
    print("=" * 116)
    rows = []
    for sb in (5, 7, 10):
        for tp in (1.0, 1.5, 2.0):
            for me in (1.2, 1.8):
                fr = []
                for p in (7, 10, 14):
                    t = run(m30, supertrend(m30, p, me), supertrend(m30, p, 3.8), sb, tp)
                    if t is not None:
                        fr.append(t.pnl / 3)
                if not fr:
                    continue
                s = stat(pd.concat(fr).sort_index(), f"M30 s{sb} TP1:{tp:g} e{me}")
                if s:
                    rows.append(s)
    fast_df = pd.DataFrame(rows).sort_values("Ret/DD", ascending=False)
    print(fast_df.head(12).to_string(index=False))

    best = fast_df.iloc[0]["sleeve"]
    sb = int(best.split("s")[1].split()[0])
    tp = float(best.split("TP1:")[1].split()[0])
    me = float(best.split("e")[-1])
    fr = []
    for p in (7, 10, 14):
        t = run(m30, supertrend(m30, p, me), supertrend(m30, p, 3.8), sb, tp)
        if t is not None:
            fr.append(t.pnl / 3)
    fast_pnl = pd.concat(fr).sort_index()

    print("\n" + "=" * 116)
    print("B. KORELASI dua sleeve (PnL bulanan) — inti dari apakah diversifikasi berguna")
    print("=" * 116)
    ms = slow_pnl.resample("ME").sum()
    mf = fast_pnl.resample("ME").sum()
    both = pd.concat([ms.rename("lambat"), mf.rename("cepat")], axis=1).fillna(0)
    both = both[(both != 0).any(axis=1)]
    corr = both["lambat"].corr(both["cepat"])
    print(f"  Korelasi bulanan lambat vs cepat : {corr:+.3f}")
    print("  (<0.3 = diversifier sejati; >0.6 = kembar, tidak menolong)")
    print(f"  Bulan lambat merah & cepat hijau : "
          f"{int(((both.lambat < 0) & (both.cepat > 0)).sum())} dari {len(both)}")

    print("\n" + "=" * 116)
    print("C. GABUNGAN (bobot setara) vs masing-masing sendirian")
    print("=" * 116)
    out = [stat(slow_pnl, "LAMBAT H1 TP1:4 sendiri"),
           stat(fast_pnl, f"CEPAT {best} sendiri")]
    for wf in (0.3, 0.5, 0.7):
        comb = pd.concat([slow_pnl * (1 - wf), fast_pnl * wf]).sort_index()
        out.append(stat(comb, f"GABUNG {int((1-wf)*100)}/{int(wf*100)} lambat/cepat"))
    res = pd.DataFrame([o for o in out if o])
    print(res.to_string(index=False))

    best_c = res.iloc[2:].sort_values("Ret/DD", ascending=False).iloc[0]
    print(f"\n  Gabungan terbaik : {best_c['sleeve']}")
    print(f"    maxDD {best_c['maxDD%']}%  vs lambat sendiri {res.iloc[0]['maxDD%']}%")
    print(f"    Ret/DD {best_c['Ret/DD']}  vs lambat sendiri {res.iloc[0]['Ret/DD']}")
    if abs(best_c["maxDD%"]) < abs(res.iloc[0]["maxDD%"]):
        print("    >> DIVERSIFIKASI BEKERJA: DD gabungan lebih kecil dari sleeve tunggal.")
    else:
        print("    >> Diversifikasi TIDAK menurunkan DD (korelasi terlalu tinggi).")

    res.to_csv(r"C:\Quant\_MONITOR\eterna_diversify.csv", index=False)
    print("\nDisimpan: C:\\Quant\\_MONITOR\\eterna_diversify.csv")


if __name__ == "__main__":
    main()
