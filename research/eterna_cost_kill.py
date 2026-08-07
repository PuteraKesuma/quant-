"""ETERNA fase-5: UJI PEMBUNUH — sensitivitas biaya + walk-forward.

Fase-4 menyisakan SATU kandidat jujur: M30 / mult_e 1.2 / struktur 20 / TP 1:3 / sesi NY
(6/6 tahun hijau, positif di kedua regime dalam R DAN dolar, n=1658, maxDD < profit).
Tapi PF-nya cuma 1.08 — tidak ada ruang napas.

Dua ancaman yang harus diuji SEBELUM kandidat ini boleh dipercaya:

1. BIAYA. Fase-4 memakai $0.35/trade. Untuk XAU 0.03 lot, spread $0.25 saja = $0.75/trade.
   Dengan 1658 trade, selisihnya ~$660 — cukup menghapus mayoritas profit.
   Diuji: $0.35 (optimis) / $0.75 (realistis FBS) / $1.20 (pesimis/slippage NY).

2. MULTIPLE TESTING. Hari ini sudah 458 konfigurasi diuji. Sebagian akan tampak bagus
   karena kebetulan. Obatnya: walk-forward anchored — latih di masa lalu, nilai HANYA di
   masa depan yang belum pernah dilihat, jendela demi jendela. Kalau edge cuma artefak
   pencarian, dia akan hancur di sini.

Skrip ini dirancang untuk MEMBUNUH kandidat, bukan menyelamatkannya.

Jalankan: python research/eterna_cost_kill.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT = 0.03, 100.0
MIN_SL_DIST = 0.50

# kandidat yang lolos fase-4 (positif di kedua regime, dalam R dan dolar)
CANDIDATES = [
    ("M30", "30min", 1.2, 20, 3.0, "ny"),      # <- kandidat utama, 6/6 tahun hijau
    ("M30", "30min", 1.2, 10, 3.0, "ny"),
    ("M30", "30min", 1.2, 20, 4.0, "ny"),
    ("M30", "30min", 1.2, 10, 4.0, "ny"),
    ("H1",  "1h",    1.8, 10, 3.0, "asia"),    # avgR tertinggi, tapi maxDD > profit
]
COSTS = [0.35, 0.75, 1.20]
SESSIONS = {"asia": (23, 30, 4, 0), "ny": (11, 30, 16, 0)}


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
    sh, sm, eh, em = sess
    mins = idx.hour * 60 + idx.minute
    s, e = sh * 60 + sm, eh * 60 + em
    return (mins >= s) & (mins < e) if s < e else (mins >= s) | (mins < e)


def run(df, st_e, struct_bars, tp_r, sess):
    """Kembalikan trade TANPA biaya; biaya diterapkan belakangan supaya bisa disapu."""
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    ok = in_session(df.index, sess)
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    swing_lo = df["low"].rolling(struct_bars).min().shift(1).to_numpy()
    swing_hi = df["high"].rolling(struct_bars).max().shift(1).to_numpy()
    pos, entry, sl, tp, ei, tr = 0, 0.0, 0.0, 0.0, 0, []

    for i in range(1, len(df)):
        if pos != 0:
            hit = None
            if pos == 1:
                if l[i] <= sl:
                    hit = sl
                elif tp and h[i] >= tp:
                    hit = tp
            else:
                if h[i] >= sl:
                    hit = sl
                elif tp and l[i] <= tp:
                    hit = tp
            if hit is not None:
                tr.append((df.index[ei], pos, entry, hit, abs(entry - sl))); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos != 0 and pos != s:
            tr.append((df.index[ei], pos, entry, o[i], abs(entry - sl))); pos = 0
        if pos != 0 or not ok[i]:
            continue
        px = o[i]
        raw = swing_lo[i] if s == 1 else swing_hi[i]
        if np.isnan(raw):
            continue
        dist = abs(px - raw)
        if dist < MIN_SL_DIST:
            continue
        pos, entry, ei = s, px, i
        sl = px - dist if s == 1 else px + dist
        tp = (px + tp_r * dist if s == 1 else px - tp_r * dist) if tp_r else 0.0

    t = pd.DataFrame(tr, columns=["t_in", "dir", "px_in", "px_out", "risk"])
    t["gross"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT
    return t


def apply_cost(t, cost):
    x = t.copy()
    x["pnl"] = x.gross - cost
    x["R"] = x.pnl / (x.risk * LOT * CONTRACT)
    return x


def metrics(t):
    if len(t) == 0:
        return None
    w, l = t.loc[t.pnl > 0, "pnl"], t.loc[t.pnl <= 0, "pnl"]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    eq = t.pnl.cumsum()
    yr = t.groupby(t.t_in.dt.year).pnl.sum()
    return {"n": len(t), "net": round(t.pnl.sum(), 0), "PF": round(pf, 2),
            "avgR": round(t.R.mean(), 4), "maxDD": round((eq - eq.cummax()).min(), 0),
            "hijau": f"{int((yr>0).sum())}/{len(yr)}"}


def main():
    df1m = load_1m()
    cache = {}

    print("=" * 108)
    print("UJI 1 — SENSITIVITAS BIAYA (yang membunuh sistem PF tipis)")
    print("=" * 108)
    print(f"{'kandidat':38} {'biaya':>7} {'n':>6} {'net$':>9} {'PF':>6} {'avgR':>9} {'maxDD':>9} {'hijau':>7}")
    print("-" * 108)

    raw = {}
    for tf_l, tf, me, sb, tpr, sess_n in CANDIDATES:
        if tf not in cache:
            cache[tf] = resample(df1m, tf)
        d = cache[tf]
        key = f"{tf_l} m{me} s{sb} TP{tpr:g} {sess_n}"
        t = run(d, supertrend(d, 10, me), sb, tpr, SESSIONS[sess_n])
        raw[key] = t
        for c in COSTS:
            m = metrics(apply_cost(t, c))
            print(f"{key:38} {c:7.2f} {m['n']:6} {m['net']:9.0f} {m['PF']:6.2f} "
                  f"{m['avgR']:+9.4f} {m['maxDD']:9.0f} {m['hijau']:>7}")
        print("-" * 108)

    print("\n" + "=" * 108)
    print("UJI 2 — WALK-FORWARD ANCHORED @ biaya realistis $0.75")
    print("Latih sampai akhir tahun N, nilai HANYA tahun N+1 (belum pernah dilihat).")
    print("=" * 108)
    print(f"{'kandidat':38} {'tahun diuji':>12} {'n':>6} {'net$':>9} {'PF':>6} {'avgR':>9}")
    print("-" * 108)
    for key, t in raw.items():
        tc = apply_cost(t, 0.75)
        wins = 0; tot = 0
        for yr in [2022, 2023, 2024, 2025, 2026]:
            seg = tc[tc.t_in.dt.year == yr]
            if len(seg) < 30:
                continue
            m = metrics(seg)
            tot += 1
            if m["net"] > 0:
                wins += 1
            print(f"{key:38} {yr:>12} {m['n']:6} {m['net']:9.0f} {m['PF']:6.2f} {m['avgR']:+9.4f}")
        print(f"{'':38} {'HASIL':>12} {wins}/{tot} tahun hijau di luar sampel")
        print("-" * 108)

    print("\n" + "=" * 108)
    print("VONIS")
    print("=" * 108)
    for key, t in raw.items():
        m035 = metrics(apply_cost(t, 0.35))
        m075 = metrics(apply_cost(t, 0.75))
        m120 = metrics(apply_cost(t, 1.20))
        survives = (m075["net"] > 0) and (m075["PF"] > 1.10) and (abs(m075["maxDD"]) < m075["net"])
        status = "LOLOS" if survives else "GUGUR"
        print(f"{key:38} {status}   net@0.35=${m035['net']:>7.0f}  "
              f"net@0.75=${m075['net']:>7.0f}  net@1.20=${m120['net']:>7.0f}")
    print("\nSyarat LOLOS: net>0 DAN PF>1.10 DAN maxDD lebih kecil dari net, pada biaya $0.75.")


if __name__ == "__main__":
    main()
