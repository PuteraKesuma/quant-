"""ORB di XAUUSD — SATU tes atas aturan yang SUDAH TERVALIDASI, di pasar baru.

KENAPA INI SAH DAN BUKAN PENYAPUAN:
ORB adalah satu-satunya sleeve di buku ini yang lolos audit tanpa catatan kaki -
687 trade, 0 dari 6 tahun rugi, PF 1,30, tahan biaya sampai 4x lipat dan slippage
5 poin, paruh kedua lebih baik dari paruh pertama. Kalau mekanisme opening-range
breakout itu nyata, dia seharusnya muncul juga di pasar lain.

Menguji satu aturan tetap di pasar baru = 1 percobaan tambahan. Menyapu parameternya
di pasar baru = puluhan. Skrip ini melakukan yang pertama: PARAMETER PERSIS SAMA
dengan yang terpasang di NAS100, tidak satu pun disetel ulang.

  range 30 menit | gate SMA50 harian | RR 1:1 | breakeven +0,5R | tutup 20:00 UTC

TIGA SESI DIUJI, karena "jam buka" untuk emas tidak sejelas indeks AS. Ketiganya
ditetapkan dari alasan struktural, bukan dicari mana yang terbaik:
  - NY   13:30 UTC  (sama dengan slot NAS100; jam data AS & pembukaan Comex)
  - London 07:00 UTC (likuiditas Eropa masuk; sesi emas fisik terbesar)
  - Asia  00:00 UTC  (pembukaan Shanghai/Tokyo)
Tiga sesi = 3 percobaan. Itu dilaporkan apa adanya di DSR, tidak disembunyikan.

KRITERIA LULUS — ditulis sebelum dijalankan:
  1. PF >= 1,15 setelah biaya
  2. untung di >= 4 dari 6 tahun
  3. |korelasi| ke ORB-NAS100 dan ke ETERNA < 0,30
  4. maxDD (lot 0.01, modal $548) <= -25%
Gagal salah satu = ditolak, tidak ada penyetelan penyelamatan.

CATATAN KONSENTRASI: kalau lulus, dia tetap sleeve XAU KETIGA di buku yang sudah
85% risikonya di emas. Lulus di sini berarti "layak dipertimbangkan", bukan
"langsung pasang".

Jalankan: python research/orb_di_xau.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from audit_live_strategies import load_m1, to_d1

# --- parameter ORB, DISALIN dari slot orb30_nas. Tidak satu pun diubah. ---
RANGE_MENIT = 30
TREND_SMA = 50
BREAKEVEN_R = 0.5
TUTUP_JAM = 20
BIAYA_PT = 0.30        # XAU 0.01 lot: ~30 sen emas bolak-balik
USD_PER_PT = 1.0       # XAU 0.01 lot = $1 per $1 gerak
CAPITAL = 548.19


def buka_ny(d: dt.date) -> int:
    et = dt.datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo("America/New_York"))
    return 13 * 60 + 30 if et.dst() != dt.timedelta(0) else 14 * 60 + 30


def orb_xau(m1: pd.DataFrame, sesi: str) -> pd.Series:
    H, L, C = m1["high"].values, m1["low"].values, m1["close"].values
    mod = m1.index.hour.values * 60 + m1.index.minute.values
    dord = m1.index.normalize().asi8
    uniq, starts = np.unique(dord, return_index=True)
    starts = list(starts) + [len(m1)]

    d1 = to_d1(m1)
    dc = d1["close"]
    pc = dc.shift(1)
    sma = dc.rolling(TREND_SMA).mean().shift(1)
    tmap = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts]))
                        else (1 if pc.loc[ts] > sma.loc[ts] else -1)) for ts in d1.index}

    rows = []
    for di in range(len(uniq)):
        a, b = starts[di], starts[di + 1]
        day = m1.index[a].date()
        if sesi == "ny":
            om = buka_ny(day)
        elif sesi == "london":
            om = 7 * 60
        else:
            om = 0
        md = mod[a:b]; idx = np.arange(a, b)
        rm = (md >= om) & (md < om + RANGE_MENIT)
        if rm.sum() < RANGE_MENIT // 2:
            continue
        ri = idx[rm]
        oh, ol = H[ri].max(), L[ri].min()
        size = oh - ol
        if size <= 0:
            continue

        batas = TUTUP_JAM * 60 if sesi != "asia" else 12 * 60
        pidx = idx[(md >= om + RANGE_MENIT) & (md < batas)]
        ei = d = ent = None
        for i in pidx:
            if H[i] > oh: ei, d, ent = i, 1, oh; break
            if L[i] < ol: ei, d, ent = i, -1, ol; break
        if ei is None:
            continue
        td = tmap.get(day, 0)
        if td == 0 or (td > 0) != (d == 1):
            continue

        armed = False
        pnl_pt = None
        for j in range(ei, b):
            if mod[j] >= batas:
                pnl_pt = d * (C[j] - ent); break
            if d == 1:
                if not armed and (H[j] - ent) >= BREAKEVEN_R * size: armed = True
                if armed and L[j] <= ent: pnl_pt = 0.0; break
                if L[j] <= ent - size: pnl_pt = -size; break
                if H[j] >= ent + size: pnl_pt = size; break
            else:
                if not armed and (ent - L[j]) >= BREAKEVEN_R * size: armed = True
                if armed and H[j] >= ent: pnl_pt = 0.0; break
                if H[j] >= ent + size: pnl_pt = -size; break
                if L[j] <= ent - size: pnl_pt = size; break
        if pnl_pt is None:
            pnl_pt = d * (C[b - 1] - ent)
        rows.append((m1.index[ei], pnl_pt * USD_PER_PT - BIAYA_PT))

    return pd.Series([v for _, v in rows], index=pd.DatetimeIndex([t for t, _ in rows]))


def dsr(r, n_trials):
    r = np.asarray(r, float); n = len(r)
    if n < 12 or r.std(ddof=1) == 0:
        return np.nan
    sr = r.mean() / r.std(ddof=1)
    sk, ku = stats.skew(r), stats.kurtosis(r, fisher=False)
    e = np.euler_gamma
    sr0 = np.sqrt(1.0 / (n - 1)) * ((1 - e) * stats.norm.ppf(1 - 1.0 / n_trials)
                                    + e * stats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    den = np.sqrt(1 - sk * sr + (ku - 1) / 4.0 * sr ** 2)
    if den <= 0 or np.isnan(den):
        return np.nan
    return float(stats.norm.cdf((sr - sr0) * np.sqrt(n - 1) / den))


def main():
    print("Memuat XAUUSD M1 ...", flush=True)
    m1 = load_m1("XAUUSD")

    print("\n" + "=" * 100)
    print("ORB dengan parameter PERSIS NAS100, dijalankan di XAUUSD (lot 0.01, modal $548)")
    print("=" * 100)
    print(f"  {'sesi':<10}{'n':>6}{'net$':>10}{'PF':>7}{'winrate':>9}{'maxDD%':>9}{'thn +':>8}{'DSR':>8}")

    hasil = {}
    for sesi in ("ny", "london", "asia"):
        s = orb_xau(m1, sesi)
        hasil[sesi] = s
        if len(s) < 30:
            print(f"  {sesi:<10}{len(s):>6}   (terlalu sedikit trade)")
            continue
        eq = CAPITAL + s.cumsum()
        dd = float(((eq - eq.cummax()) / eq.cummax()).min())
        w, l = s[s > 0].sum(), -s[s < 0].sum()
        thn = s.groupby(s.index.year).sum()
        d = dsr(s.resample("ME").sum(), 3)
        print(f"  {sesi:<10}{len(s):>6}{s.sum():>10.2f}{(w/l if l else 99):>7.2f}"
              f"{100*(s>0).mean():>8.0f}%{100*dd:>9.1f}{int((thn>0).sum()):>6}/{len(thn)}"
              f"{d if d==d else 0:>8.3f}")

    # ---- kandidat terbaik: rincian + korelasi ----
    best = max(hasil, key=lambda k: hasil[k].sum() if len(hasil[k]) >= 30 else -1e9)
    s = hasil[best]
    print(f"\n" + "=" * 100)
    print(f"RINCIAN sesi terbaik: {best.upper()}")
    print("=" * 100)
    print(f"  {'tahun':<8}{'n':>6}{'net$':>10}{'PF':>7}{'winrate':>9}")
    for y, g in s.groupby(s.index.year):
        w, l = g[g > 0].sum(), -g[g < 0].sum()
        print(f"  {y:<8}{len(g):>6}{g.sum():>10.2f}{(w/l if l else 99):>7.2f}{100*(g>0).mean():>8.0f}%")

    from blocking_akurat import load_h1, eterna_trades
    from portfolio_audit import nas_dollars
    et = eterna_trades(load_h1()).set_index("masuk").pnl
    orbn = nas_dollars()
    if orbn.index.tz is None:
        orbn.index = orbn.index.tz_localize("UTC")
    j = pd.DataFrame({"ORB_nas": orbn.resample("ME").sum(),
                      "ETERNA": et.resample("ME").sum(),
                      "ORB_xau": s.resample("ME").sum()}).fillna(0.0)
    c1 = j["ORB_xau"].corr(j["ORB_nas"]); c2 = j["ORB_xau"].corr(j["ETERNA"])
    print(f"\n  korelasi bulanan: vs ORB_nas {c1:+.3f}   vs ETERNA {c2:+.3f}")

    print("\n" + "=" * 100)
    print("VONIS terhadap kriteria yang ditulis di depan")
    print("=" * 100)
    w, l = s[s > 0].sum(), -s[s < 0].sum()
    pf = w / l if l else 99
    thn = s.groupby(s.index.year).sum()
    eq = CAPITAL + s.cumsum()
    dd = 100 * float(((eq - eq.cummax()) / eq.cummax()).min())
    cek = [("PF >= 1.15", pf >= 1.15, f"{pf:.2f}"),
           ("untung >= 4/6 tahun", int((thn > 0).sum()) >= 4, f"{int((thn>0).sum())}/{len(thn)}"),
           ("|korelasi| < 0.30 keduanya", abs(c1) < 0.30 and abs(c2) < 0.30, f"{c1:+.2f} / {c2:+.2f}"),
           ("maxDD >= -25%", dd >= -25, f"{dd:.1f}%")]
    for nama, ok, nilai in cek:
        print(f"  [{'v' if ok else 'X'}] {nama:<28} {nilai}")
    print(f"\n  >> {'LULUS SEMUA - kandidat sleeve ketiga' if all(o for _,o,_ in cek) else 'DITOLAK'}")
    if all(o for _, o, _ in cek):
        print("     Catatan: tetap sleeve XAU ketiga di buku yang sudah 85% risikonya di emas.")
        print("     Lulus di sini berarti layak dipertimbangkan, bukan langsung dipasang.")
    print("=" * 100)


if __name__ == "__main__":
    main()
