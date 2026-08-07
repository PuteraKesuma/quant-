"""ETERNA fase-12: bikin manajemen posisi lebih pintar (STRUKTURAL, bukan tambah knob).

Walk-forward fase-11 mengajarkan dua hal:
  (a) MENYETEL ULANG parameter merugikan (ensemble tetap $2126 vs pilih-1 $1258).
      -> Jangan optimasi ulang. Kunci parameter, ratakan lewat ensemble.
  (b) Mode 'conservative' terpilih 5/5 jendela = edge ada di STRUKTUR, bukan angka.
      Tapi TP 1:4 terpilih 4/5 dan entry x2.5 terpilih 4/5, sementara ensemble kita
      memakai TP 1:3 dan entry x1.8. Ensemble kita mungkin kurang lebar.

Yang diuji di sini SEMUANYA struktural (mengubah bentuk manajemen posisi), bukan
menambah parameter untuk dicocok-cocokkan:

  1. ENSEMBLE LEBAR   — rentangkan mencakup entry {1.8, 2.5} x TP {3, 4} (32 anggota).
  2. BREAKEVEN        — geser SL ke entry setelah profit 1R (sistem user pakai ini di Z).
  3. PARTIAL TP       — tutup separuh di 1R atau 1.5R, sisanya lari ke TP penuh.
  4. BUFFER SL        — SL struktur + 0.25/0.5 x ATR, supaya tidak kena sapuan tepat di swing.
  5. TIME EXIT        — tutup paksa setelah N bar kalau belum kena SL/TP.

Setiap varian dinilai TIDAK dengan net saja, tapi Ret/DD + tahun hijau + netSide
(regime sideways 2021-2023), supaya perbaikan semu ketahuan.

Jalankan: python research/eterna_smarter_exits.py
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

ATRS = [7, 10, 14, 20]
MULT_T = [3.8, 5.0]
NARROW = [(p, 1.8, mt, 3.0) for p in ATRS for mt in MULT_T]                      # fase-9
WIDE = [(p, me, mt, tp) for p in ATRS for me in (1.8, 2.5)
        for mt in MULT_T for tp in (3.0, 4.0)]                                    # 32 anggota


def load_h1():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts").resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def atr_series(df, n):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def supertrend(df, period, mult):
    a = atr_series(df, period)
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


def run(df, st_e, st_t, tp_r, be_at=None, partial_at=None, partial_frac=0.5,
        sl_buffer_atr=0.0, atr_buf=None, max_bars=None):
    """Mode konservatif + SL struktur, dengan opsi breakeven / partial TP / buffer / time exit."""
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    slo = df["low"].rolling(STRUCT).min().shift(1).to_numpy()
    shi = df["high"].rolling(STRUCT).max().shift(1).to_numpy()
    ab = atr_buf.to_numpy() if atr_buf is not None else None

    pos = 0; entry = sl = tp = risk0 = 0.0; ei = 0
    part_done = False; out = []

    def close(idx, price, frac):
        out.append((df.index[ei], pos, entry, price, risk0, frac))

    for i in range(1, len(df)):
        if pos != 0:
            # partial TP lebih dulu (target lebih dekat)
            if partial_at and not part_done:
                lvl = entry + partial_at * risk0 if pos == 1 else entry - partial_at * risk0
                touched = (h[i] >= lvl) if pos == 1 else (l[i] <= lvl)
                stopped = (l[i] <= sl) if pos == 1 else (h[i] >= sl)
                if touched and not stopped:
                    close(i, lvl, partial_frac)
                    part_done = True
                    if be_at is None:
                        sl = entry            # sisa posisi diamankan ke BE
            hit = None
            if pos == 1:
                hit = sl if l[i] <= sl else (tp if h[i] >= tp else None)
            else:
                hit = sl if h[i] >= sl else (tp if l[i] <= tp else None)
            if hit is None and max_bars and (i - ei) >= max_bars:
                hit = o[i]
            if hit is not None:
                close(i, hit, 1.0 - (partial_frac if part_done else 0.0))
                pos = 0; part_done = False
            elif be_at:
                prof = (h[i] - entry) if pos == 1 else (entry - l[i])
                if prof >= be_at * risk0:
                    if pos == 1 and sl < entry:
                        sl = entry
                    elif pos == -1 and sl > entry:
                        sl = entry

        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            close(i, o[i], 1.0 - (partial_frac if part_done else 0.0))
            pos = 0; part_done = False
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s:
            continue
        px = o[i]
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        buf = (sl_buffer_atr * ab[i]) if (ab is not None and not np.isnan(ab[i])) else 0.0
        dist = abs(px - raw) + buf
        if dist < MIN_SL_DIST:
            continue
        pos, entry, ei, risk0 = s, px, i, dist
        sl = px - dist if s == 1 else px + dist
        tp = px + tp_r * dist if s == 1 else px - tp_r * dist
        part_done = False

    if not out:
        return None
    t = pd.DataFrame(out, columns=["t_in", "dir", "px_in", "px_out", "risk", "frac"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT * t.frac - COST * t.frac
    return t.set_index("t_in")[["pnl", "risk"]]


def ens(df, members, sts, **kw):
    parts = []
    for (p, me, mt, tpr) in members:
        t = run(df, sts[(p, me)], sts[(p, mt)], tpr, **kw)
        if t is not None and len(t):
            x = t.copy(); x["pnl"] /= len(members)
            parts.append(x)
    return pd.concat(parts).sort_index() if parts else None


def stats(c, label):
    if c is None or len(c) < 50:
        return None
    eq = c.pnl.cumsum()
    dd = (eq - eq.cummax()).min()
    w, l = c.loc[c.pnl > 0, "pnl"], c.loc[c.pnl <= 0, "pnl"]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    yr = c.groupby(c.index.year).pnl.sum()
    m = c.pnl.resample("ME").sum(); m = m[m != 0]
    side = c.loc[SIDEWAYS[0]:SIDEWAYS[1]]
    return {"varian": label, "n": len(c), "net": round(c.pnl.sum()),
            "PF": round(pf, 2), "maxDD": round(dd),
            "RetDD": round((c.pnl.sum() / 5.5) / abs(dd), 2) if dd else np.nan,
            "hijau": f"{int((yr>0).sum())}/{len(yr)}",
            "merah%": round(100 * (m < 0).mean()),
            "netSide": round(side.pnl.sum())}


def main():
    d = load_h1()
    sts = {}
    for p in ATRS:
        for m in (1.8, 2.5, 3.8, 5.0):
            sts[(p, m)] = supertrend(d, p, m)
    a14 = atr_series(d, 14)
    print(f"H1 {len(d):,} bar | biaya ${COST}\n")

    rows = []
    print("Menghitung varian ...", flush=True)
    rows.append(stats(ens(d, NARROW, sts), "BASELINE fase-9 (8 anggota, TP1:3)"))
    rows.append(stats(ens(d, WIDE, sts), "1. ENSEMBLE LEBAR (32: e1.8/2.5 x TP3/4)"))
    for be in (1.0, 1.5):
        rows.append(stats(ens(d, WIDE, sts, be_at=be), f"2. lebar + breakeven @{be}R"))
    for pa in (1.0, 1.5):
        rows.append(stats(ens(d, WIDE, sts, partial_at=pa), f"3. lebar + partial 50% @{pa}R"))
    for bf in (0.25, 0.5):
        rows.append(stats(ens(d, WIDE, sts, sl_buffer_atr=bf, atr_buf=a14),
                          f"4. lebar + buffer SL {bf}xATR"))
    for mb in (24, 72):
        rows.append(stats(ens(d, WIDE, sts, max_bars=mb), f"5. lebar + time exit {mb} bar"))
    # kombinasi paling menjanjikan
    rows.append(stats(ens(d, WIDE, sts, partial_at=1.0, be_at=1.0),
                      "6. lebar + partial@1R + BE@1R"))
    rows = [r for r in rows if r]

    df = pd.DataFrame(rows)
    base = df.iloc[0]
    print("\n" + "=" * 118)
    print("HASIL — diurut Ret/DD")
    print("=" * 118)
    print(df.sort_values("RetDD", ascending=False).to_string(index=False))

    print("\n" + "=" * 118)
    print("PERBANDINGAN vs BASELINE fase-9")
    print("=" * 118)
    for _, r in df.iterrows():
        if r["varian"] == base["varian"]:
            continue
        dn = 100 * (r["net"] - base["net"]) / abs(base["net"])
        dr = r["RetDD"] - base["RetDD"]
        mark = "LEBIH BAIK" if (r["RetDD"] > base["RetDD"] * 1.10 and r["netSide"] > 0) else \
               ("setara" if abs(dr) < 0.15 else "lebih buruk")
        print(f"  {r['varian']:44} net {dn:+6.1f}%  Ret/DD {dr:+.2f}  "
              f"sideways ${r['netSide']:+5}  -> {mark}")
    df.to_csv(r"C:\Quant\_MONITOR\eterna_smarter_exits.csv", index=False)
    print("\nDisimpan: C:\\Quant\\_MONITOR\\eterna_smarter_exits.csv")


if __name__ == "__main__":
    main()
