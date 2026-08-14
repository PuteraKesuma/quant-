"""SMC BERBASIS SESI — cari peluang di TIAP sesi market (permintaan user 2026-08-14).

MASALAH DENGAN YANG SEKARANG: H1-C memindai terus-menerus tanpa mengenal sesi, dan
menghasilkan 0.151 trade/hari (85% hari NOL). User ingin 1-2 trade/hari dengan
peluang dicari di setiap sesi.

IDE YANG DIUJI — ini cara SMC dipakai sebenarnya, bukan pelonggaran aturan:
    Asia membentuk RANGE -> sesi berikutnya MENYAPU likuiditas di ujung range itu
    -> harga berbalik -> masuk searah pembalikan.

  1. Range Asia  : high/low 00:00-06:00 UTC
  2. Jendela buru: London 07:00-11:00 UTC, New York 13:00-17:00 UTC
  3. SWEEP       : wick menembus level referensi (high/low Asia, atau high/low
                   sesi London untuk jendela NY) LALU close balik ke dalam
  4. KONFIRMASI  : BOS M5 melawan arah sweep dalam `konfirm` bar M5
  5. ENTRY       : close bar konfirmasi. SL di luar ekstrem sweep + buffer.
                   TP = rr x risiko.
  6. BATAS       : maksimal 1 trade per SESI, maksimal 2 per HARI.

Aturan ini secara struktural memberi maksimal 2 peluang/hari (satu per jendela),
jadi frekuensinya datang dari RANCANGAN, bukan dari melonggarkan filter.

ANTI-LOOKAHEAD: range Asia baru dipakai SETELAH 06:00 UTC. Sweep dinilai dari bar
M5 yang SUDAH TERTUTUP. Level sesi London untuk jendela NY baru dipakai setelah
11:00 UTC.

Biaya nyata: spread $0.50/trade, swap LONG -$0.6995/malam, SHORT +$0.2491, Rabu 3x.

Jalankan: python research/smc_sesi.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from smc_xau_backtest import (load_m1, tf, swing_pivots, malam,
                              LOT, SPREAD, SWAP_LONG, SWAP_SHORT, CAPITAL)

ASIA = (0, 6)            # UTC: pembentukan range
LONDON = (7, 11)         # jendela buru 1
NY = (13, 17)            # jendela buru 2


def jalankan(m5: pd.DataFrame, *, konfirm=12, rr=2.0, buffer_frac=0.15,
             k5=3, maks_per_hari=2, pakai_london=True, pakai_ny=True) -> pd.DataFrame:
    hi = m5["high"].to_numpy(); lo = m5["low"].to_numpy()
    c = m5["close"].to_numpy(); o = m5["open"].to_numpy()
    idx = m5.index
    jam = idx.hour.to_numpy()
    hari = idx.normalize()
    n = len(m5)

    sh, sl = swing_pivots(m5, k5)

    def lvl(piv):
        out = np.full(n, np.nan); p = 0; cur = np.nan
        for j in range(n):
            while p < len(piv) and piv[p][2] <= j:
                cur = piv[p][1]; p += 1
            out[j] = cur
        return out
    L_sh, L_sl = lvl(sh), lvl(sl)

    trades = []
    pos = 0; entry = p_sl = p_tp = 0.0; ei = 0
    hari_kini = None; n_hari = 0
    sesi_dipakai = set()
    asia_hi = asia_lo = None
    lon_hi = lon_lo = None

    for j in range(1, n):
        d = hari[j]
        if d != hari_kini:
            hari_kini = d; n_hari = 0; sesi_dipakai = set()
            asia_hi = asia_lo = lon_hi = lon_lo = None

        # --- kelola posisi ---
        if pos != 0:
            if pos == 1:
                if lo[j] <= p_sl:
                    trades.append((idx[ei], idx[j], 1, entry, p_sl, "SL")); pos = 0
                elif hi[j] >= p_tp:
                    trades.append((idx[ei], idx[j], 1, entry, p_tp, "TP")); pos = 0
            else:
                if hi[j] >= p_sl:
                    trades.append((idx[ei], idx[j], -1, entry, p_sl, "SL")); pos = 0
                elif lo[j] <= p_tp:
                    trades.append((idx[ei], idx[j], -1, entry, p_tp, "TP")); pos = 0

        h = jam[j]
        # --- bangun range Asia (bar yang sudah tertutup) ---
        if ASIA[0] <= h < ASIA[1]:
            asia_hi = hi[j] if asia_hi is None else max(asia_hi, hi[j])
            asia_lo = lo[j] if asia_lo is None else min(asia_lo, lo[j])
            continue
        # --- range London (untuk dipakai jendela NY) ---
        if LONDON[0] <= h < LONDON[1]:
            lon_hi = hi[j] if lon_hi is None else max(lon_hi, hi[j])
            lon_lo = lo[j] if lon_lo is None else min(lon_lo, lo[j])

        if pos != 0 or n_hari >= maks_per_hari:
            continue

        # --- pilih jendela + level referensi ---
        if pakai_london and LONDON[0] <= h < LONDON[1]:
            sesi = "LON"; ref_hi, ref_lo = asia_hi, asia_lo
        elif pakai_ny and NY[0] <= h < NY[1]:
            sesi = "NY"
            ref_hi = max(x for x in (asia_hi, lon_hi) if x is not None) \
                if (asia_hi or lon_hi) else None
            ref_lo = min(x for x in (asia_lo, lon_lo) if x is not None) \
                if (asia_lo or lon_lo) else None
        else:
            continue
        if sesi in sesi_dipakai or ref_hi is None or ref_lo is None:
            continue

        # --- SWEEP: wick menembus, close balik ke dalam ---
        arah = 0
        if hi[j] > ref_hi and c[j] < ref_hi:
            arah = -1; ekstrem = hi[j]        # sapu atas -> cari SHORT
        elif lo[j] < ref_lo and c[j] > ref_lo:
            arah = 1; ekstrem = lo[j]         # sapu bawah -> cari LONG
        if arah == 0:
            continue

        # --- KONFIRMASI: BOS M5 searah pembalikan, dalam `konfirm` bar ---
        j_in = None
        for t in range(j, min(j + konfirm, n)):
            ref = L_sh[t] if arah == 1 else L_sl[t]
            if ref != ref:
                continue
            if (arah == 1 and c[t] > ref) or (arah == -1 and c[t] < ref):
                j_in = t; break
        if j_in is None:
            continue

        px = c[j_in]
        buf = abs(px - ekstrem) * buffer_frac
        s = ekstrem - buf if arah == 1 else ekstrem + buf
        if abs(px - s) < 1.0:                 # SL terlalu ketat untuk emas
            continue
        t_ = px + rr * abs(px - s) if arah == 1 else px - rr * abs(px - s)
        pos, entry, ei, p_sl, p_tp = arah, px, j_in, s, t_
        n_hari += 1; sesi_dipakai.add(sesi)

    t = pd.DataFrame(trades, columns=["masuk", "keluar", "arah", "px_in", "px_out", "sebab"])
    if len(t) == 0:
        return t
    t["kotor"] = (t.px_out - t.px_in) * t.arah * LOT * 100
    t["malam"] = [malam(a, b) for a, b in zip(t.masuk, t.keluar)]
    t["swap"] = np.where(t.arah == 1, t.malam * SWAP_LONG, t.malam * SWAP_SHORT)
    t["pnl"] = t.kotor - SPREAD + t.swap
    return t


def ringkas(t, label, n_hari):
    if len(t) < 20:
        return {"varian": label, "n": len(t), "per hari": 0.0, "net$": 0.0,
                "PF": 0.0, "WR%": 0, "maxDD%": 0.0, "net/DD": 0.0, "thn+": "-"}
    d = t.set_index("masuk").pnl
    eq = CAPITAL + d.cumsum()
    dd = abs(float(((eq - eq.cummax()) / eq.cummax()).min()))
    w, l = d[d > 0].sum(), -d[d < 0].sum()
    thn = d.groupby(d.index.year).sum()
    return {"varian": label, "n": len(d), "per hari": round(len(d) / n_hari, 2),
            "net$": round(d.sum(), 2), "PF": round(w / l if l else 99, 2),
            "WR%": round(100 * (d > 0).mean()), "maxDD%": round(-100 * dd, 1),
            "net/DD": round(d.sum() / (100 * dd), 1) if dd else 0.0,
            "thn+": f"{int((thn > 0).sum())}/{len(thn)}"}


def main():
    print("Membangun ...", flush=True)
    m1 = load_m1(); m5 = tf(m1, "5min")
    hd = pd.Series(m1.index.normalize().unique())
    n_hari = int((hd.dt.weekday < 5).sum())
    print(f"  M5 {len(m5):,} bar, {n_hari} hari perdagangan")

    print("\n" + "=" * 104)
    print("SMC SESI: sapu likuiditas range Asia -> konfirmasi M5 -> masuk")
    print("=" * 104)
    rows = []
    for konf in (6, 12, 24):
        for rr in (1.5, 2.0, 3.0):
            t = jalankan(m5, konfirm=konf, rr=rr)
            rows.append(ringkas(t, f"konfirm {konf} bar, rr {rr}", n_hari))
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n" + "=" * 104)
    print("PECAH PER JENDELA (setelan terbaik menurut net/DD)")
    print("=" * 104)
    best = max(rows, key=lambda r: r["net/DD"] if r["n"] >= 20 else -9e9)
    print("  acuan:", best["varian"])
    konf = int(best["varian"].split()[1]); rr = float(best["varian"].split()[-1])
    for lab, lon, ny in (("London saja", True, False), ("New York saja", False, True),
                         ("keduanya", True, True)):
        t = jalankan(m5, konfirm=konf, rr=rr, pakai_london=lon, pakai_ny=ny)
        print(pd.DataFrame([ringkas(t, lab, n_hari)]).to_string(index=False,
              header=(lab == "London saja")))

    print("\n" + "=" * 104)
    print("SEBARAN TRADE PER HARI (setelan terbaik)")
    print("=" * 104)
    t = jalankan(m5, konfirm=konf, rr=rr)
    per = t.set_index("masuk").pnl.groupby(t.set_index("masuk").index.normalize()).size()
    n0 = n_hari - len(per)
    print(f"  0 trade : {n0:5d} ({100*n0/n_hari:.1f}%)")
    for k in sorted(per.unique()):
        cc = (per == k).sum()
        print(f"  {k} trade : {cc:5d} ({100*cc/n_hari:.1f}%)")
    print("\n" + "=" * 104)


if __name__ == "__main__":
    main()
