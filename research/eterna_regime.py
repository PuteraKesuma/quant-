"""ETERNA fase-3: pisahkan EDGE dari KEBERUNTUNGAN REGIME.

Fase-2 menemukan tanda bahaya: avgR OOS (2025-26) jauh MELAMPAUI IS (2021-24) —
sampai 47x. Itu terbalik dari semestinya. Dugaan: yang terukur di OOS adalah reli
besar emas 2024-2026, bukan edge. Sistem trend-follower always-in APA PUN terlihat
hebat di pasar trending.

Uji di sini:
1. REGIME SPLIT — emas sideways (2021-2023) vs emas bull (2024-2026). Kalau edge cuma
   ada di regime bull, ini bukan strategi, ini taruhan arah emas.
2. PER-TAHUN — konsistensi, bukan total.
3. MONTE CARLO block-bootstrap (blok 20 trade, 5000 path) — berapa P(rugi) dan p5.
4. BENCHMARK: bandingkan dengan 'selalu long' di periode sama. Kalau strateginya tidak
   mengalahkan buy&hold di regime bull, dia cuma beta emas yang dibungkus indikator.

Kandidat = plateau yang ditemukan fase-2 (sesi Asia, mode agresif, multiplier rendah).

Jalankan: python research/eterna_regime.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
SL_POINTS, POINT, LOT, CONTRACT, COST = 1000, 0.01, 0.03, 100.0, 0.35
MULT_TREND = 3.8
ASIA = (23, 30, 4, 0)

CANDIDATES = [
    ("H1", "1h", 7, 1.2), ("H1", "1h", 10, 1.2), ("H1", "1h", 14, 1.2), ("H1", "1h", 20, 1.2),
    ("M30", "30min", 7, 1.8), ("M30", "30min", 10, 1.8),
    ("M30", "30min", 14, 1.8), ("M30", "30min", 20, 1.8),
]

REGIMES = {
    "sideways_2021_2023": (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")),
    "bull_2024_2026":     (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-07-01", tz="UTC")),
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


def in_asia(idx):
    mins = idx.hour * 60 + idx.minute
    s, e = ASIA[0] * 60 + ASIA[1], ASIA[2] * 60 + ASIA[3]
    return (mins >= s) | (mins < e)


def run(df, st_e):
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    ok = in_asia(df.index)
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    sl = SL_POINTS * POINT
    pos, entry, ei, tr = 0, 0.0, 0, []
    for i in range(1, len(df)):
        if pos != 0:
            if pos == 1 and l[i] <= entry - sl:
                tr.append((df.index[ei], pos, entry, entry - sl)); pos = 0
            elif pos == -1 and h[i] >= entry + sl:
                tr.append((df.index[ei], pos, entry, entry + sl)); pos = 0
        s = sd[i]
        if np.isnan(s) or not ok[i]:
            continue
        s = int(s)
        if pos == 0:
            pos, entry, ei = s, o[i], i
        elif pos != s:
            tr.append((df.index[ei], pos, entry, o[i])); pos, entry, ei = s, o[i], i
    t = pd.DataFrame(tr, columns=["t_in", "dir", "px_in", "px_out"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    t["R"] = t.pnl / (sl * LOT * CONTRACT)
    return t


def mc_bootstrap(pnl, n_path=5000, block=20, seed=42):
    """Block bootstrap: P(net<0) dan persentil, mempertahankan autokorelasi."""
    rng = np.random.default_rng(seed)
    x = pnl.to_numpy()
    n = len(x)
    if n < block * 2:
        return None
    nb = int(np.ceil(n / block))
    starts = rng.integers(0, n - block, size=(n_path, nb))
    paths = np.empty((n_path, nb * block))
    for j in range(nb):
        idx = starts[:, j][:, None] + np.arange(block)[None, :]
        paths[:, j*block:(j+1)*block] = x[idx]
    paths = paths[:, :n]
    nets = paths.sum(axis=1)
    return {"p5": np.percentile(nets, 5), "p50": np.percentile(nets, 50),
            "p95": np.percentile(nets, 95), "p_neg": float((nets < 0).mean())}


def main():
    df1m = load_1m()
    print("BENCHMARK — gerak emas per regime (buy & hold 0.03 lot):")
    for name, (a, b) in REGIMES.items():
        seg = df1m[(df1m.index >= a) & (df1m.index < b)]
        move = (seg["close"].iloc[-1] - seg["close"].iloc[0]) * LOT * CONTRACT
        print(f"  {name:22} {seg['close'].iloc[0]:8.1f} -> {seg['close'].iloc[-1]:8.1f}   "
              f"buy&hold = ${move:+,.0f}")

    cache = {}
    print("\n" + "=" * 104)
    print(f"{'konfigurasi':28} {'regime':22} {'n':>5} {'net$':>9} {'PF':>6} {'avgR':>8} {'WR%':>6}")
    print("-" * 104)

    results = {}
    for tf_label, tf, p, me in CANDIDATES:
        if tf not in cache:
            cache[tf] = resample(df1m, tf)
        d = cache[tf]
        t = run(d, supertrend(d, p, me))
        key = f"{tf_label} atr{p} m{me}"
        results[key] = t
        for name, (a, b) in REGIMES.items():
            s = t[(t.t_in >= a) & (t.t_in < b)]
            if len(s) == 0:
                continue
            w, l = s.loc[s.pnl > 0, "pnl"], s.loc[s.pnl <= 0, "pnl"]
            pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
            print(f"{key:28} {name:22} {len(s):5} {s.pnl.sum():9.0f} {pf:6.2f} "
                  f"{s.R.mean():+8.3f} {100*(s.pnl>0).mean():6.1f}")
        print("-" * 104)

    print("\n" + "=" * 104)
    print("PER TAHUN (net $, lot tetap 0.03):")
    yr = pd.DataFrame({k: v.groupby(v.t_in.dt.year).pnl.sum() for k, v in results.items()})
    print(yr.round(0).to_string())
    print("\nJumlah tahun hijau per konfigurasi:")
    print((yr > 0).sum().to_string())

    print("\n" + "=" * 104)
    print("MONTE CARLO block-bootstrap (5000 path, blok 20 trade) — SELURUH periode:")
    print(f"{'konfigurasi':28} {'p5':>10} {'median':>10} {'p95':>10} {'P(rugi)':>10}")
    for k, t in results.items():
        m = mc_bootstrap(t.pnl)
        if m:
            print(f"{k:28} {m['p5']:10.0f} {m['p50']:10.0f} {m['p95']:10.0f} {m['p_neg']*100:9.1f}%")

    print("\n" + "=" * 104)
    print("MONTE CARLO — HANYA regime sideways 2021-2023 (uji paling keras):")
    a, b = REGIMES["sideways_2021_2023"]
    for k, t in results.items():
        s = t[(t.t_in >= a) & (t.t_in < b)]
        m = mc_bootstrap(s.pnl)
        if m:
            print(f"{k:28} {m['p5']:10.0f} {m['p50']:10.0f} {m['p95']:10.0f} {m['p_neg']*100:9.1f}%")
        else:
            print(f"{k:28} sampel terlalu kecil")

    pd.concat({k: v for k, v in results.items()}, names=["config"]).to_csv(
        r"C:\Quant\_MONITOR\eterna_regime_trades.csv")
    print("\nTrade tersimpan: C:\\Quant\\_MONITOR\\eterna_regime_trades.csv")


if __name__ == "__main__":
    main()
