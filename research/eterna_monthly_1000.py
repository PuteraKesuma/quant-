"""ETERNA fase-6: distribusi BULANAN di skala nyata user — modal $1000, lot 0.01.

Menjawab langsung permintaan user: "strategy eterna yang setiap bulannya profit".
Semua angka di-skalakan ke lot 0.01 (biaya ikut turun proporsional: spread x 0.01 x 100).

Kandidat = dua yang bertahan paling lama di fase-5 (walau keduanya GUGUR uji pembunuh):
  A. M30 / 1.2 / struktur 20 / TP 1:3 / NY  -> 5/5 tahun hijau walk-forward, tapi PF 1.06
  B. H1  / 1.8 / struktur 10 / TP 1:3 / Asia -> PF 1.18, tapi maxDD > profit dan n cuma 214

Jalankan: python research/eterna_monthly_1000.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT = 0.01, 100.0          # <- skala user
COST = 0.25                          # spread ~$0.25 pada 0.01 lot XAU
CAPITAL = 1000.0
MIN_SL_DIST = 0.50
SESSIONS = {"asia": (23, 30, 4, 0), "ny": (11, 30, 16, 0)}
CANDS = [("A. M30 TP1:3 NY", "30min", 1.2, 20, 3.0, "ny"),
         ("B. H1 TP1:3 Asia", "1h", 1.8, 10, 3.0, "asia")]


def load_1m():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def atr(df, n):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def supertrend(df, period, mult):
    a = atr(df, period)
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


def in_session(idx, sess):
    sh, sm, eh, em = sess
    mins = idx.hour * 60 + idx.minute
    s, e = sh * 60 + sm, eh * 60 + em
    return (mins >= s) & (mins < e) if s < e else (mins >= s) | (mins < e)


def run(df, st_e, sb, tp_r, sess):
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    ok = in_session(df.index, sess)
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    slo = df["low"].rolling(sb).min().shift(1).to_numpy()
    shi = df["high"].rolling(sb).max().shift(1).to_numpy()
    pos, entry, sl, tp, ei, tr = 0, 0.0, 0.0, 0.0, 0, []
    for i in range(1, len(df)):
        if pos != 0:
            hit = None
            if pos == 1:
                hit = sl if l[i] <= sl else (tp if tp and h[i] >= tp else None)
            else:
                hit = sl if h[i] >= sl else (tp if tp and l[i] <= tp else None)
            if hit is not None:
                tr.append((df.index[ei], pos, entry, hit)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos != 0 and pos != s:
            tr.append((df.index[ei], pos, entry, o[i])); pos = 0
        if pos != 0 or not ok[i]:
            continue
        px = o[i]
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(px - raw)
        if dist < MIN_SL_DIST:
            continue
        pos, entry, ei = s, px, i
        sl = px - dist if s == 1 else px + dist
        tp = (px + tp_r * dist if s == 1 else px - tp_r * dist) if tp_r else 0.0
    t = pd.DataFrame(tr, columns=["t_in", "dir", "px_in", "px_out"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    return t


def main():
    df1m = load_1m()
    print(f"MODAL ${CAPITAL:,.0f}  |  LOT {LOT}  |  biaya ${COST}/trade\n")

    for name, tf, me, sb, tpr, sess in CANDS:
        d = df1m.resample(tf, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        t = run(d, supertrend(d, 10, me), sb, tpr, SESSIONS[sess])
        m = t.set_index("t_in").pnl.resample("ME").sum()
        m = m[m != 0]
        eq = t.pnl.cumsum()
        dd = (eq - eq.cummax()).min()

        red = int((m < 0).sum())
        print("=" * 78)
        print(f"{name}   ({len(t)} trade, {len(m)} bulan kalender)")
        print("=" * 78)
        print(f"  Total 5,5 tahun    : ${t.pnl.sum():+,.0f}  ({100*t.pnl.sum()/CAPITAL:+.1f}% dari modal)")
        print(f"  Rata-rata per bulan: ${m.mean():+,.2f}   median ${m.median():+,.2f}")
        print(f"  BULAN MERAH        : {red} dari {len(m)}  ({100*red/len(m):.0f}%)")
        print(f"  Bulan terbaik      : ${m.max():+,.0f}")
        print(f"  Bulan TERBURUK     : ${m.min():+,.0f}  ({100*m.min()/CAPITAL:+.1f}% dari modal)")
        print(f"  maxDD              : ${dd:,.0f}  ({100*abs(dd)/CAPITAL:.1f}% dari modal)")
        yr = t.set_index("t_in").pnl.resample("YE").sum()
        print(f"  Return per tahun   : ${yr.mean():+,.0f}  ({100*yr.mean()/CAPITAL:+.1f}%/tahun)")
        rr = (yr.mean() / abs(dd)) if dd else np.nan
        print(f"  Return/DD          : {rr:.2f}   <- di bawah 0.5 = tidak layak dijalankan")
        print(f"\n  Beruntun merah terpanjang: ", end="")
        streak = mx = 0
        for v in m:
            streak = streak + 1 if v < 0 else 0
            mx = max(mx, streak)
        print(f"{mx} bulan berturut-turut")
        print("\n  Distribusi bulanan (setiap karakter = 1 bulan, + hijau / - merah):")
        print("   ", "".join("+" if v > 0 else "-" for v in m))
    print("\n" + "=" * 78)


if __name__ == "__main__":
    main()
