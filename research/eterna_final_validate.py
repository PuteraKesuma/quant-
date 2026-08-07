"""ETERNA fase-9: VALIDASI PENENTU atas plateau konservatif H1.

Fase-8 (setelah user benar menuntut celah ditutup) menemukan plateau NYATA:
  H1 / mult_entry 1.8 / mode KONSERVATIF / SL struktur 20 / TP 1:3 / semua sesi
  dengan ATR 7,10,14,20 dan mult_tren 3.8,5.0 -> SEMUA sehat, 4 di antaranya 6/6 tahun hijau.

Kandidat sebelumnya (M30 s20TP3) RUNTUH saat di-ensemble -> terbukti titik keberuntungan.
Plateau ini harus lewat uji yang SAMA:

  UJI 1 ENSEMBLE  — gabung 8 tetangga setara. Plateau nyata: hasil BERTAHAN.
                    Keberuntungan: hasil runtuh (seperti s20TP3: 7.2% -> 3.5%).
  UJI 2 BIAYA     — $0.25 / $0.50 / $0.80 per trade @0.01 lot.
  UJI 3 MC        — block-bootstrap, terutama HANYA regime sideways 2021-2023.
  UJI 4 BULANAN   — distribusi jujur di modal $1000.
  UJI 5 WALK-FWD  — per tahun, di luar sampel.

Jalankan: python research/eterna_final_validate.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT = 0.01, 100.0
CAPITAL = 1000.0
MIN_SL_DIST, STRUCT, TP_R = 0.50, 20, 3.0
MULT_E = 1.8
NEIGHBOURS = [(p, mt) for p in (7, 10, 14, 20) for mt in (3.8, 5.0)]
COSTS = [0.25, 0.50, 0.80]
SIDEWAYS = (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"))
BULL = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-07-01", tz="UTC"))


def load_h1():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")
    return df.resample("1h", label="left", closed="left").agg(
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


def run_conservative(df, st_e, st_t):
    """Mode KONSERVATIF: hanya entry searah tren; sinyal lawan = tutup saja."""
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    slo = df["low"].rolling(STRUCT).min().shift(1).to_numpy()
    shi = df["high"].rolling(STRUCT).max().shift(1).to_numpy()
    pos, entry, sl, tp, ei, out = 0, 0.0, 0.0, 0.0, 0, []
    for i in range(1, len(df)):
        if pos != 0:
            hit = None
            if pos == 1:
                hit = sl if l[i] <= sl else (tp if h[i] >= tp else None)
            else:
                hit = sl if h[i] >= sl else (tp if l[i] <= tp else None)
            if hit is not None:
                out.append((df.index[ei], pos, entry, hit, abs(entry - sl))); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((df.index[ei], pos, entry, o[i], abs(entry - sl))); pos = 0
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s:
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
        tp = px + TP_R * dist if s == 1 else px - TP_R * dist
    t = pd.DataFrame(out, columns=["t_in", "dir", "px_in", "px_out", "risk"])
    t["gross"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT
    return t


def cost(t, c):
    x = t.copy(); x["pnl"] = x.gross - c
    x["R"] = x.pnl / (x.risk * LOT * CONTRACT)
    return x


def stats(t):
    if len(t) == 0:
        return None
    w, l = t.loc[t.pnl > 0, "pnl"], t.loc[t.pnl <= 0, "pnl"]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    eq = t.pnl.cumsum()
    yr = t.groupby(t.t_in.dt.year).pnl.sum()
    dd = (eq - eq.cummax()).min()
    return {"n": len(t), "net": round(t.pnl.sum()), "PF": round(pf, 2),
            "avgR": round(t.R.mean(), 4), "maxDD": round(dd),
            "RetDD": round((t.pnl.sum() / 5.5) / abs(dd), 2) if dd else np.nan,
            "hijau": f"{int((yr > 0).sum())}/{len(yr)}"}


def mc(pnl, n_path=5000, block=20, seed=7):
    rng = np.random.default_rng(seed)
    x = pnl.to_numpy(); n = len(x)
    if n < block * 2:
        return None
    nb = int(np.ceil(n / block))
    st = rng.integers(0, n - block, size=(n_path, nb))
    paths = np.empty((n_path, nb * block))
    for j in range(nb):
        idx = st[:, j][:, None] + np.arange(block)[None, :]
        paths[:, j*block:(j+1)*block] = x[idx]
    nets = paths[:, :n].sum(axis=1)
    return {"p5": round(np.percentile(nets, 5)), "p50": round(np.percentile(nets, 50)),
            "p_neg": round(100 * float((nets < 0).mean()), 1)}


def main():
    d = load_h1()
    print(f"H1: {len(d):,} bar\n")
    raws = {}
    for p, mt in NEIGHBOURS:
        raws[f"atr{p}_mt{mt:g}"] = run_conservative(d, supertrend(d, p, MULT_E),
                                                    supertrend(d, p, mt))

    print("=" * 104)
    print("UJI 1 — ENSEMBLE (uji yang membunuh kandidat sebelumnya) @ biaya $0.25")
    print("=" * 104)
    print(f"{'anggota':22} {'n':>5} {'net$':>8} {'PF':>6} {'maxDD':>8} {'Ret/DD':>7} {'hijau':>7}")
    ind = []
    for k, t in raws.items():
        s = stats(cost(t, 0.25))
        ind.append(s)
        print(f"{k:22} {s['n']:5} {s['net']:8} {s['PF']:6.2f} {s['maxDD']:8} "
              f"{s['RetDD']:7.2f} {s['hijau']:>7}")
    comb = pd.concat([cost(t, 0.25)[["t_in", "pnl", "risk"]].assign(
        pnl=lambda x: x.pnl / len(raws)) for t in raws.values()]).sort_values("t_in")
    comb["R"] = comb.pnl / (comb.risk * LOT * CONTRACT)
    se = stats(comb)
    print("-" * 104)
    print(f"{'ENSEMBLE (8 setara)':22} {se['n']:5} {se['net']:8} {se['PF']:6.2f} "
          f"{se['maxDD']:8} {se['RetDD']:7.2f} {se['hijau']:>7}")
    avg_ind = np.mean([s["net"] for s in ind])
    print(f"\nRata-rata net anggota tunggal : ${avg_ind:,.0f}")
    print(f"Net ensemble                  : ${se['net']:,.0f}")
    keep = 100 * se["net"] / avg_ind if avg_ind else 0
    print(f"Ensemble mempertahankan       : {keep:.0f}% dari rata-rata anggota")
    print("  >90% = plateau NYATA.  <60% = titik keberuntungan (nasib s20TP3).")

    print("\n" + "=" * 104)
    print("UJI 2 — SENSITIVITAS BIAYA (ensemble)")
    print("=" * 104)
    for c in COSTS:
        cm = pd.concat([cost(t, c)[["t_in", "pnl", "risk"]].assign(
            pnl=lambda x: x.pnl / len(raws)) for t in raws.values()]).sort_values("t_in")
        cm["R"] = cm.pnl / (cm.risk * LOT * CONTRACT)
        s = stats(cm)
        print(f"  biaya ${c:.2f}  net=${s['net']:>6}  PF={s['PF']:.2f}  "
              f"maxDD=${s['maxDD']:>5}  Ret/DD={s['RetDD']:.2f}  hijau={s['hijau']}")

    print("\n" + "=" * 104)
    print("UJI 3 — MONTE CARLO (ensemble, biaya $0.50)")
    print("=" * 104)
    cm = pd.concat([cost(t, 0.50)[["t_in", "pnl", "risk"]].assign(
        pnl=lambda x: x.pnl / len(raws)) for t in raws.values()]).sort_values("t_in")
    for label, seg in [("SELURUH periode", cm),
                       ("HANYA sideways 2021-2023",
                        cm[(cm.t_in >= SIDEWAYS[0]) & (cm.t_in < SIDEWAYS[1])]),
                       ("HANYA bull 2024-2026",
                        cm[(cm.t_in >= BULL[0]) & (cm.t_in < BULL[1])])]:
        m = mc(seg.pnl)
        if m:
            print(f"  {label:26} p5=${m['p5']:>6}  median=${m['p50']:>6}  P(rugi)={m['p_neg']:>5}%")

    print("\n" + "=" * 104)
    print("UJI 4 — DISTRIBUSI BULANAN @ modal $1000, biaya $0.50")
    print("=" * 104)
    mth = cm.set_index("t_in").pnl.resample("ME").sum()
    mth = mth[mth != 0]
    streak = mx = 0
    for v in mth:
        streak = streak + 1 if v < 0 else 0
        mx = max(mx, streak)
    eq = cm.pnl.cumsum()
    print(f"  Total          : ${cm.pnl.sum():+,.0f}  ({100*cm.pnl.sum()/CAPITAL:+.0f}% modal)")
    print(f"  Per tahun      : ${cm.pnl.sum()/5.5:+,.0f}  ({100*(cm.pnl.sum()/5.5)/CAPITAL:+.1f}%/thn)")
    print(f"  Bulan merah    : {int((mth<0).sum())} dari {len(mth)}  ({100*(mth<0).mean():.0f}%)")
    print(f"  Merah beruntun : {mx} bulan")
    print(f"  Bulan terburuk : ${mth.min():+,.0f}  ({100*mth.min()/CAPITAL:+.1f}% modal)")
    print(f"  maxDD          : ${(eq-eq.cummax()).min():,.0f} "
          f"({100*abs((eq-eq.cummax()).min())/CAPITAL:.1f}% modal)")
    print("\n  " + "".join("+" if v > 0 else "-" for v in mth))

    print("\n" + "=" * 104)
    print("UJI 5 — PER TAHUN (ensemble, biaya $0.50)")
    print("=" * 104)
    yr = cm.set_index("t_in").pnl.resample("YE").sum()
    for ts, v in yr.items():
        bar = "#" * max(0, int(v / 25))
        print(f"  {ts.year}  ${v:+8,.0f}  {bar}")


if __name__ == "__main__":
    main()
