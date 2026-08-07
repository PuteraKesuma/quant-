"""ETERNA fase-11: WALK-FORWARD ANCHORED yang sesungguhnya.

Fase-9 memvalidasi ensemble H1 konservatif, tapi seluruh pemilihan parameter memakai
SELURUH data. Walk-forward menjawab pertanyaan yang lebih keras: kalau kita hanya boleh
melihat MASA LALU saat memilih, apakah pilihan itu tetap untung di masa depan?

Tiga jalur dibandingkan di jendela OOS yang SAMA:

  A. ENSEMBLE TETAP    — 8 anggota yang sudah ditetapkan, TIDAK pernah disetel ulang.
  B. PILIH-1 TERBAIK   — tiap jendela, pilih SATU konfigurasi terbaik dari data latih
                          saja, lalu perdagangkan di jendela uji. Ini yang dilakukan
                          kebanyakan trader ("optimasi ulang tiap tahun").
  C. ENSEMBLE TOP-5    — tiap jendela, ambil 5 terbaik dari data latih, gabung setara.

Kalau A >= B, itu bukti kuat bahwa MENYETEL ULANG parameter justru merusak, dan bahwa
edge-nya ada di struktur strategi — bukan di angka parameternya. Ini konsisten dengan
uji ensemble fase-9 (retensi 100%).

Jendela anchored (melebar): latih 2021..N-1, uji tahun N, untuk N = 2022..2026.

Efisiensi: seluruh daftar trade tiap konfigurasi dihitung SEKALI, lalu diiris per
jendela. Tidak ada kebocoran karena pemilihan hanya memakai irisan data latih.

Jalankan: python research/eterna_walkforward.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.01, 100.0, 0.50
MIN_SL_DIST, STRUCT = 0.50, 20

ATRS = [7, 10, 14, 20]
MULT_E = [1.2, 1.8, 2.5]
MULT_T = [3.8, 5.0]
TP_RS = [2.0, 3.0, 4.0]
MODES = ["conservative", "direct"]

# ensemble tetap hasil fase-9
FIXED = [(p, 1.8, mt, 3.0, "conservative") for p in ATRS for mt in MULT_T]
WINDOWS = [2022, 2023, 2024, 2025, 2026]


def load_h1():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts").resample("1h", label="left", closed="left").agg(
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


def run(df, st_e, st_t, tp_r, mode):
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    slo = df["low"].rolling(STRUCT).min().shift(1).to_numpy()
    shi = df["high"].rolling(STRUCT).max().shift(1).to_numpy()
    pos, entry, sl, tp, risk0, ei, out = 0, 0.0, 0.0, 0.0, 0.0, 0, []
    for i in range(1, len(df)):
        if pos != 0:
            hit = None
            if pos == 1:
                hit = sl if l[i] <= sl else (tp if h[i] >= tp else None)
            else:
                hit = sl if h[i] >= sl else (tp if l[i] <= tp else None)
            if hit is not None:
                out.append((df.index[ei], pos, entry, hit, risk0)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((df.index[ei], pos, entry, o[i], risk0)); pos = 0
        if pos != 0:
            continue
        if mode == "conservative" and (np.isnan(td[i]) or int(td[i]) != s):
            continue
        px = o[i]
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(px - raw)
        if dist < MIN_SL_DIST:
            continue
        pos, entry, ei, risk0 = s, px, i, dist
        sl = px - dist if s == 1 else px + dist
        tp = px + tp_r * dist if s == 1 else px - tp_r * dist
    if not out:
        return None
    t = pd.DataFrame(out, columns=["t_in", "dir", "px_in", "px_out", "risk"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    t["R"] = t.pnl / (t.risk * LOT * CONTRACT)
    return t.set_index("t_in")


def score(t):
    """Kriteria pemilihan di data LATIH: avgR, minimal 60 trade."""
    if t is None or len(t) < 60:
        return -9e9
    return t.R.mean()


def perf(frames):
    """Gabung setara beberapa daftar trade -> metrik."""
    if not frames:
        return None
    parts = [f[["pnl"]].copy() / len(frames) for f in frames if f is not None and len(f)]
    if not parts:
        return None
    c = pd.concat(parts).sort_index()
    eq = c.pnl.cumsum()
    w, l = c.loc[c.pnl > 0, "pnl"], c.loc[c.pnl <= 0, "pnl"]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    return {"n": len(c), "net": c.pnl.sum(), "PF": pf,
            "maxDD": (eq - eq.cummax()).min()}


def main():
    d = load_h1()
    print(f"H1 {len(d):,} bar | biaya ${COST}/trade\n")

    st_cache = {}
    for p in ATRS:
        for m in set(MULT_E + MULT_T):
            st_cache[(p, m)] = supertrend(d, p, m)

    print("Menghitung seluruh konfigurasi grid (sekali saja) ...")
    grid = {}
    for p in ATRS:
        for me in MULT_E:
            for mt in MULT_T:
                for tpr in TP_RS:
                    for mode in MODES:
                        if mode == "direct" and mt != MULT_T[0]:
                            continue          # direct tak pakai gate -> hindari duplikat
                        t = run(d, st_cache[(p, me)], st_cache[(p, mt)], tpr, mode)
                        if t is not None and len(t) >= 100:
                            grid[(p, me, mt, tpr, mode)] = t
    print(f"  {len(grid)} konfigurasi\n")

    print("=" * 106)
    print("WALK-FORWARD ANCHORED — latih 2021..N-1, uji HANYA tahun N")
    print("=" * 106)
    print(f"{'thn uji':>8} | {'A. ENSEMBLE TETAP':>26} | {'B. PILIH-1 TERBAIK':>34} | "
          f"{'C. TOP-5':>18}")
    print(f"{'':>8} | {'net$':>8} {'PF':>6} {'n':>5} | {'net$':>8} {'PF':>6}  {'dipilih':>16} | "
          f"{'net$':>8} {'PF':>6}")
    print("-" * 106)

    tot = {"A": [], "B": [], "C": []}
    picks = []
    for yr in WINDOWS:
        tr_end = pd.Timestamp(f"{yr}-01-01", tz="UTC")
        te_end = pd.Timestamp(f"{yr+1}-01-01", tz="UTC")

        # --- A: ensemble tetap, tanpa penyetelan ---
        a = perf([grid[k].loc[tr_end:te_end] for k in FIXED if k in grid])

        # --- B & C: pemilihan HANYA dari data latih ---
        scored = []
        for k, t in grid.items():
            scored.append((score(t.loc[:tr_end]), k))
        scored.sort(reverse=True)
        best_k = scored[0][1]
        top5 = [k for _, k in scored[:5]]
        picks.append((yr, best_k))

        b = perf([grid[best_k].loc[tr_end:te_end]])
        c = perf([grid[k].loc[tr_end:te_end] for k in top5])

        for tag, r in (("A", a), ("B", b), ("C", c)):
            if r:
                tot[tag].append(r["net"])
        bk = f"a{best_k[0]} e{best_k[1]} t{best_k[2]} TP{best_k[3]:g} {best_k[4][:4]}"
        print(f"{yr:>8} | {a['net']:8.0f} {a['PF']:6.2f} {a['n']:5} | "
              f"{b['net']:8.0f} {b['PF']:6.2f}  {bk:>16} | {c['net']:8.0f} {c['PF']:6.2f}")

    print("-" * 106)
    print(f"{'TOTAL':>8} | {sum(tot['A']):8.0f} {'':6} {'':5} | "
          f"{sum(tot['B']):8.0f} {'':6}  {'':>16} | {sum(tot['C']):8.0f}")
    print(f"{'hijau':>8} | {sum(1 for v in tot['A'] if v>0)}/{len(tot['A'])}"
          f"{'':>16} | {sum(1 for v in tot['B'] if v>0)}/{len(tot['B'])}"
          f"{'':>26} | {sum(1 for v in tot['C'] if v>0)}/{len(tot['C'])}")

    print("\n" + "=" * 106)
    print("VONIS WALK-FORWARD")
    print("=" * 106)
    A, B, C = sum(tot["A"]), sum(tot["B"]), sum(tot["C"])
    print(f"  A. Ensemble TETAP (tanpa setel ulang) : ${A:>8,.0f}")
    print(f"  B. Pilih-1 terbaik, disetel tiap thn  : ${B:>8,.0f}")
    print(f"  C. Ensemble top-5, disetel tiap thn   : ${C:>8,.0f}")
    if A >= B:
        print(f"\n  >> MENYETEL ULANG MERUGIKAN: ensemble tetap unggul ${A-B:,.0f} atas pilih-1.")
        print("     Artinya edge ada di STRUKTUR strategi, bukan di angka parameternya.")
        print("     Best practice: KUNCI parameter, jangan optimasi ulang tiap tahun.")
    else:
        print(f"\n  >> Pilih-1 unggul ${B-A:,.0f}. Perlu diperiksa apakah stabil atau kebetulan.")

    print("\n  Parameter yang DIPILIH tiap tahun (kalau berubah-ubah = tak ada yang stabil):")
    for yr, k in picks:
        print(f"    {yr}: ATR {k[0]:>2}  entry x{k[1]}  tren x{k[2]}  TP 1:{k[3]:g}  {k[4]}")
    uniq = len(set(picks_k for _, picks_k in picks))
    print(f"\n  Konfigurasi berbeda terpilih: {uniq} dari {len(picks)} jendela.")
    if uniq > len(picks) / 2:
        print("  >> Pilihan 'terbaik' BERUBAH-UBAH tiap tahun = tidak ada satu pun yang stabil.")
        print("     Ini alasan teknis kenapa ensemble tetap lebih dipercaya.")


if __name__ == "__main__":
    main()
