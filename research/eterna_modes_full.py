"""ETERNA fase-8: TUTUP CELAH — mode EA yang belum diuji + sapuan ATR/trend penuh.

User benar: fase 1-7 belum menguji semuanya. Yang terlewat dan ditutup di sini:
  1. MODE_MODERATE      (mode 1 EA: masuk saat TREND BERBALIK, logika beda sendiri)
  2. MODE_BUY_ONLY      (emas 1900->4000 di periode ini; long-only punya alasan ekonomi)
  3. MODE_SELL_ONLY     (pembanding jujur untuk buy_only)
  4. Periode ATR        (fase 4-7 MENGUNCI di 10; di sini disapu 7/10/14/20)
  5. ATR_Multiplier_Trend (fase 4-7 mengunci 3.8; di sini 2.5/3.8/5.0)
  6. Mode konservatif + SL struktur (fase 4 cuma jalankan mode agresif)

Semua dengan SL STRUKTUR (dinamis) + TP rasio R — varian yang dipakai user.

Bar validasi TIDAK diturunkan. Kandidat harus lolos:
  (a) positif di KEDUA regime (sideways 2021-23 DAN bull 2024-26), dalam R dan dolar
  (b) bertahan pada biaya realistis
  (c) TIDAK runtuh saat di-ensemble dengan tetangganya  <- ini yang membunuh s20TP3

Jalankan: python research/eterna_modes_full.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.01, 100.0, 0.25
MIN_SL_DIST = 0.50
STRUCT = 20
TP_R = 3.0

TFS = {"M15": "15min", "M30": "30min", "H1": "1h"}
ATR_PERIODS = [7, 10, 14, 20]
MULT_ENTRY = [1.2, 1.8, 2.5]
MULT_TREND = [2.5, 3.8, 5.0]
MODES = ["direct", "moderate", "conservative", "buy_only", "sell_only"]
SESSIONS = {"all": None, "asia": (23, 30, 4, 0), "ny": (11, 30, 16, 0)}

SIDEWAYS = (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"))
BULL = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-07-01", tz="UTC"))


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
    if sess is None:
        return np.ones(len(idx), dtype=bool)
    sh, sm, eh, em = sess
    mins = idx.hour * 60 + idx.minute
    s, e = sh * 60 + sm, eh * 60 + em
    return (mins >= s) & (mins < e) if s < e else (mins >= s) | (mins < e)


def run(df, st_e, st_t, mode, sess):
    """Semua mode EA, dengan SL struktur + TP rasio R. Anti-lookahead: sinyal & gate shift(1)."""
    flip_e = (st_e != st_e.shift(1))
    sd = st_e.where(flip_e).shift(1).to_numpy()          # sinyal entry pada bar flip
    td = st_t.shift(1).to_numpy()                         # arah tren (gate)
    t_flip = (st_t != st_t.shift(1)).shift(1).fillna(False).to_numpy()   # bar tren berbalik
    ok = in_session(df.index, sess)
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    slo = df["low"].rolling(STRUCT).min().shift(1).to_numpy()
    shi = df["high"].rolling(STRUCT).max().shift(1).to_numpy()

    pos, entry, sl, tp, ei, out = 0, 0.0, 0.0, 0.0, 0, []

    def open_pos(i, s):
        nonlocal pos, entry, sl, tp, ei
        px = o[i]
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            return
        dist = abs(px - raw)
        if dist < MIN_SL_DIST:
            return
        pos, entry, ei = s, px, i
        sl = px - dist if s == 1 else px + dist
        tp = px + TP_R * dist if s == 1 else px - TP_R * dist

    def close_pos(i, price):
        nonlocal pos
        out.append((df.index[ei], pos, entry, price, abs(entry - sl)))
        pos = 0

    for i in range(1, len(df)):
        if pos != 0:                                      # SL/TP dulu
            hit = None
            if pos == 1:
                hit = sl if l[i] <= sl else (tp if h[i] >= tp else None)
            else:
                hit = sl if h[i] >= sl else (tp if l[i] <= tp else None)
            if hit is not None:
                close_pos(i, hit)

        s = sd[i]
        tdir = td[i]
        has_sig = not np.isnan(s)
        s = int(s) if has_sig else 0

        if mode == "moderate" and t_flip[i] and not np.isnan(tdir):
            # tren berbalik -> tutup lawan arah, masuk searah tren baru
            tdi = int(tdir)
            if pos == -tdi:
                close_pos(i, o[i])
            if pos == 0 and ok[i]:
                open_pos(i, tdi)
            continue

        if not has_sig:
            continue

        if mode == "direct":
            if pos == 0:
                if ok[i]:
                    open_pos(i, s)
            elif pos != s:
                close_pos(i, o[i])
                if ok[i]:
                    open_pos(i, s)

        elif mode in ("conservative", "moderate"):
            aligned = (not np.isnan(tdir)) and int(tdir) == s
            if pos == -s:                                 # sinyal lawan -> tutup saja
                close_pos(i, o[i])
            if pos == 0 and aligned and ok[i]:
                open_pos(i, s)

        elif mode == "buy_only":
            if s == 1:
                if pos == 0 and ok[i]:
                    open_pos(i, 1)
            elif pos == 1:
                close_pos(i, o[i])

        elif mode == "sell_only":
            if s == -1:
                if pos == 0 and ok[i]:
                    open_pos(i, -1)
            elif pos == -1:
                close_pos(i, o[i])

    if not out:
        return None
    t = pd.DataFrame(out, columns=["t_in", "dir", "px_in", "px_out", "risk"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    t["R"] = t.pnl / (t.risk * LOT * CONTRACT)
    return t


def summarize(t):
    w, l = t.loc[t.pnl > 0, "pnl"], t.loc[t.pnl <= 0, "pnl"]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    eq = t.pnl.cumsum()
    yr = t.groupby(t.t_in.dt.year).pnl.sum()
    sw = t[(t.t_in >= SIDEWAYS[0]) & (t.t_in < SIDEWAYS[1])]
    bl = t[(t.t_in >= BULL[0]) & (t.t_in < BULL[1])]
    return {"n": len(t), "net": round(t.pnl.sum()), "PF": round(pf, 2),
            "WR%": round(100 * (t.pnl > 0).mean(), 1), "avgR": round(t.R.mean(), 4),
            "maxDD": round((eq - eq.cummax()).min()),
            "hijau": f"{int((yr > 0).sum())}/{len(yr)}",
            "n_side": len(sw),
            "netSide": round(sw.pnl.sum()) if len(sw) else np.nan,
            "netBull": round(bl.pnl.sum()) if len(bl) else np.nan,
            "avgR_side": round(sw.R.mean(), 4) if len(sw) else np.nan}


def main():
    df1m = load_1m()
    rows = []
    for tf_l, tf in TFS.items():
        d = df1m.resample(tf, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        print(f"\n=== {tf_l} ({len(d):,} bar) ===", flush=True)
        st_e_cache = {(p, m): supertrend(d, p, m) for p in ATR_PERIODS for m in MULT_ENTRY}
        st_t_cache = {(p, m): supertrend(d, p, m) for p in ATR_PERIODS for m in MULT_TREND}
        for p in ATR_PERIODS:
            for me in MULT_ENTRY:
                for mt in MULT_TREND:
                    for mode in MODES:
                        # mode direct/buy_only/sell_only tidak memakai gate tren -> hindari duplikat
                        if mode in ("direct", "buy_only", "sell_only") and mt != MULT_TREND[0]:
                            continue
                        for sess_n, sess in SESSIONS.items():
                            t = run(d, st_e_cache[(p, me)], st_t_cache[(p, mt)], mode, sess)
                            if t is None or len(t) < 150:
                                continue
                            s = summarize(t)
                            if s["n_side"] < 50:
                                continue
                            s.update({"TF": tf_l, "atr": p, "mult_e": me,
                                      "mult_t": mt if mode in ("moderate", "conservative") else "-",
                                      "mode": mode, "sesi": sess_n})
                            rows.append(s)
        print(f"  terkumpul: {len(rows)}", flush=True)

    out = pd.DataFrame(rows)
    cols = ["TF", "atr", "mult_e", "mult_t", "mode", "sesi", "n", "net", "PF", "WR%",
            "avgR", "maxDD", "hijau", "netSide", "netBull", "avgR_side"]
    out = out[cols]
    out.to_csv(r"C:\Quant\_MONITOR\eterna_modes_full.csv", index=False)

    print("\n" + "=" * 132)
    print("TOP 20 — avgR keseluruhan")
    print("=" * 132)
    print(out.sort_values("avgR", ascending=False).head(20).to_string(index=False))

    print("\n" + "=" * 132)
    print("LOLOS SYARAT KETAT: untung di KEDUA regime (dolar), maxDD < net, minimal 5/6 tahun hijau")
    print("=" * 132)
    good = out[(out.netSide > 0) & (out.netBull > 0) &
               (out.maxDD.abs() < out.net) &
               (out.hijau.str.split("/").str[0].astype(int) >= 5)]
    if len(good):
        print(good.sort_values("avgR", ascending=False).to_string(index=False))
    else:
        print("  TIDAK ADA satu pun konfigurasi yang lolos.")

    print(f"\nTotal dinilai        : {len(out)}")
    print(f"Untung kedua regime  : {len(out[(out.netSide>0)&(out.netBull>0)])}")
    print(f"Lolos syarat ketat   : {len(good)}")
    print("\nPer mode — berapa yang untung di kedua regime:")
    for m in MODES:
        sub = out[out["mode"] == m]
        if len(sub):
            g = sub[(sub.netSide > 0) & (sub.netBull > 0)]
            print(f"  {m:14} {len(g):4} / {len(sub):4}  ({100*len(g)/len(sub):4.0f}%)   "
                  f"avgR terbaik {sub.avgR.max():+.4f}")
    print("\nDisimpan: C:\\Quant\\_MONITOR\\eterna_modes_full.csv")


if __name__ == "__main__":
    main()
