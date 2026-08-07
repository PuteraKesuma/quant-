"""ETERNA fase-4: varian YANG BENAR — SL struktur (dinamis) + TP rasio R:R.

Fase 1-3 menguji setelan BAWAAN EA (SL_MANUAL 1000 poin, TP=0) dan varian itu terbukti
cuma beta emas: seluruh profitnya di reli 2024-2026, P(rugi) 41-91% di regime sideways
2021-2023, dan nyaris tidak mengalahkan buy&hold.

TAPI user memakai konfigurasi lain, dan itu strategi yang berbeda secara fundamental:
  SL_Mode = SL_STRUCTURE  -> SL = iLowest(low, ATR_Period, 1) untuk BUY
                                  iHighest(high, ATR_Period, 1) untuk SELL   (EA baris 257-271)
  TP      = kelipatan R dari jarak SL itu (user ingat ~1:4)

Bedanya fundamental: risiko & imbalan TERDEFINISI. Sistem 1:4 tidak butuh pasar trending,
dia butuh hit-rate target 4R di atas ~20%. Jadi profil regime-nya bisa beda total.

Anti-lookahead tetap: sinyal dari bar tertutup (shift 1), SL/TP dihitung dari bar SEBELUM
entry, eksekusi di open bar berikutnya. Kalau SL & TP kena di bar yang sama -> SL menang
(konservatif, karena kita tidak punya data intrabar).

Jalankan: python research/eterna_structure_rr.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.03, 100.0, 0.35
MIN_SL_DIST = 0.50          # jarak SL minimal $0.50 (hindari SL absurd ketat saat bar sempit)

TFS = {"M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h"}
STRUCT_BARS = [10, 20]      # ATR_Period dipakai EA sebagai lookback struktur
TP_R = [2.0, 3.0, 4.0, 5.0, None]   # None = tanpa TP (keluar saat sinyal balik)
MULT_ENTRY = [1.2, 1.8]
SESSIONS = {"all": None, "asia": (23, 30, 4, 0), "ny": (11, 30, 16, 0)}

SIDEWAYS = (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"))
BULL = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-07-01", tz="UTC"))


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
    if sess is None:
        return np.ones(len(idx), dtype=bool)
    sh, sm, eh, em = sess
    mins = idx.hour * 60 + idx.minute
    s, e = sh * 60 + sm, eh * 60 + em
    return (mins >= s) & (mins < e) if s < e else (mins >= s) | (mins < e)


def run(df, st_e, struct_bars, tp_r, sess):
    """SL = ekstrem struktur `struct_bars` bar SEBELUM entry. TP = tp_r x jarak SL."""
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    ok = in_session(df.index, sess)
    o = df["open"].to_numpy(); h = df["high"].to_numpy(); l = df["low"].to_numpy()
    # ekstrem struktur dari bar yang sudah tertutup (shift 1 -> tidak melihat bar berjalan)
    swing_lo = df["low"].rolling(struct_bars).min().shift(1).to_numpy()
    swing_hi = df["high"].rolling(struct_bars).max().shift(1).to_numpy()

    pos, entry, sl, tp, ei, tr = 0, 0.0, 0.0, 0.0, 0, []

    for i in range(1, len(df)):
        if pos != 0:
            hit = None
            if pos == 1:
                if l[i] <= sl:
                    hit = ("SL", sl)
                elif tp and h[i] >= tp:
                    hit = ("TP", tp)
            else:
                if h[i] >= sl:
                    hit = ("SL", sl)
                elif tp and l[i] <= tp:
                    hit = ("TP", tp)
            if hit:
                tr.append((df.index[ei], pos, entry, hit[1], abs(entry - sl), hit[0]))
                pos = 0

        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos != 0 and pos != s:      # sinyal balik -> tutup di open
            tr.append((df.index[ei], pos, entry, o[i], abs(entry - sl), "flip"))
            pos = 0
        if pos != 0 or not ok[i]:
            continue

        px = o[i]
        raw_sl = swing_lo[i] if s == 1 else swing_hi[i]
        if np.isnan(raw_sl):
            continue
        dist = abs(px - raw_sl)
        if dist < MIN_SL_DIST:
            continue
        pos, entry, ei = s, px, i
        sl = px - dist if s == 1 else px + dist
        tp = (px + tp_r * dist if s == 1 else px - tp_r * dist) if tp_r else 0.0

    if not tr:
        return None
    t = pd.DataFrame(tr, columns=["t_in", "dir", "px_in", "px_out", "risk", "why"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    t["R"] = t.pnl / (t.risk * LOT * CONTRACT)
    return t


def summarize(t):
    w, l = t.loc[t.pnl > 0, "pnl"], t.loc[t.pnl <= 0, "pnl"]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    eq = t.pnl.cumsum()
    yearly = t.groupby(t.t_in.dt.year).pnl.sum()
    sw = t[(t.t_in >= SIDEWAYS[0]) & (t.t_in < SIDEWAYS[1])]
    bl = t[(t.t_in >= BULL[0]) & (t.t_in < BULL[1])]
    return {
        "n": len(t), "net": round(t.pnl.sum(), 0), "PF": round(pf, 2),
        "WR%": round(100 * (t.pnl > 0).mean(), 1), "avgR": round(t.R.mean(), 3),
        "maxDD": round((eq - eq.cummax()).min(), 0),
        "hijau": f"{int((yearly>0).sum())}/{len(yearly)}",
        "n_side": len(sw), "avgR_side": round(sw.R.mean(), 3) if len(sw) else np.nan,
        "net_side": round(sw.pnl.sum(), 0) if len(sw) else np.nan,
        "avgR_bull": round(bl.R.mean(), 3) if len(bl) else np.nan,
        "net_bull": round(bl.pnl.sum(), 0) if len(bl) else np.nan,
    }


def main():
    df1m = load_1m()
    rows = []
    for tf_label, tf in TFS.items():
        d = resample(df1m, tf)
        print(f"\n=== {tf_label} ({len(d):,} bar) ===")
        for me in MULT_ENTRY:
            st = supertrend(d, 10, me)     # periode ATR Supertrend tetap 10 (default EA)
            for sb in STRUCT_BARS:
                for tp_r in TP_R:
                    for sess_name, sess in SESSIONS.items():
                        t = run(d, st, sb, tp_r, sess)
                        if t is None or len(t) < 150:
                            continue
                        s = summarize(t)
                        if s["n_side"] < 50:
                            continue
                        s.update({"TF": tf_label, "mult_e": me, "struct": sb,
                                  "TP_R": tp_r if tp_r else "flip", "sesi": sess_name})
                        rows.append(s)
        print(f"  total konfigurasi terkumpul: {len(rows)}")

    out = pd.DataFrame(rows)
    if out.empty:
        print("tidak ada konfigurasi lolos syarat sampel.")
        return
    cols = ["TF", "mult_e", "struct", "TP_R", "sesi", "n", "net", "PF", "WR%", "avgR",
            "maxDD", "hijau", "avgR_side", "net_side", "avgR_bull", "net_bull"]
    out = out[cols]

    out.sort_values("avgR", ascending=False).to_csv(
        r"C:\Quant\_MONITOR\eterna_structure_rr.csv", index=False)

    print("\n" + "=" * 130)
    print("TOP 20 berdasarkan avgR KESELURUHAN:")
    print(out.sort_values("avgR", ascending=False).head(20).to_string(index=False))

    print("\n" + "=" * 130)
    print("UJI SEBENARNYA — TOP 15 berdasarkan avgR di REGIME SIDEWAYS 2021-2023:")
    print("(kalau ini positif, edge-nya nyata dan bukan sekadar reli emas)")
    print(out.sort_values("avgR_side", ascending=False).head(15).to_string(index=False))

    both = out[(out.avgR_side > 0) & (out.avgR_bull > 0)]
    print(f"\nKonfigurasi POSITIF di KEDUA regime : {len(both)} dari {len(out)} "
          f"({100*len(both)/len(out):.0f}%)")
    if len(both):
        print("\nYang positif di kedua regime, diurut avgR sideways:")
        print(both.sort_values("avgR_side", ascending=False).head(15).to_string(index=False))
    print("\nDisimpan: C:\\Quant\\_MONITOR\\eterna_structure_rr.csv")


if __name__ == "__main__":
    main()
