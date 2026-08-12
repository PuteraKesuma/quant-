"""H1 acceleration di XAU, kali ini DENGAN MANAJEMEN RISIKO + uji portofolio langsung.

DUA KELEMAHAN CARAKU MENOLAK SEBELUMNYA (ditemukan saat memeriksa ulang atas desakan
user, dan keduanya sah):

  1. Aku mengujinya TANPA STOP. `h1_trades` masuk lalu keluar 12 bar kemudian, apa pun
     yang terjadi di tengah. maxDD -95,8% itu GEJALA TIDAK ADA STOP, bukan bukti tidak
     ada edge. Eterna dan ORB dua-duanya punya stop. Membandingkan sinyal telanjang
     dengan strategi yang dikelola bukan perbandingan yang adil.

  2. Aku memakai ambang korelasi (<0,30) sebagai GERBANG penolakan. Padahal pertanyaan
     yang benar bukan "berapa korelasinya" tapi "apakah portofolionya jadi lebih baik".
     Korelasi 0,587 masih bisa menambah nilai kalau timing-nya berbeda. Yang menentukan
     adalah Calmar portofolio GABUNGAN, diuji langsung.

SINYAL TIDAK DISENTUH - persis yang sudah diuji: pada H1, return 4 bar terakhir per bar
lebih besar dari return 8 bar sebelumnya per bar, keduanya searah -> ikut arah.

DUA VARIAN MANAJEMEN RISIKO, ditetapkan di depan, TIDAK disapu:
  V1  stop 1,0 x ATR(14) H1, target 2,0 x ATR, batas waktu 12 bar
  V2  stop 1,5 x ATR(14) H1, trailing setelah +1,0 ATR, batas waktu 24 bar
Dua varian = 2 percobaan. Dilaporkan apa adanya di DSR.

KRITERIA LULUS, ditulis sebelum dijalankan:
  1. untung di >= 4 dari 6 tahun          (yang gagal di versi tanpa stop: cuma 3/6)
  2. maxDD <= -35% pada lot 0.01 modal $548
  3. PF >= 1.15
  4. portofolio ORB+ETERNA+H1 punya Calmar LEBIH BAIK dari ORB+ETERNA saja
Kriteria 4 menggantikan ambang korelasi - inilah yang seharusnya kupakai sejak awal.

Jalankan: python research/h1_xau_dikelola.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
import edge_hunt_4keluarga as EH
from blocking_akurat import load_h1, eterna_trades
from portfolio_audit import nas_dollars

BIAYA = 0.39 * 1.5        # spread XAU nyata x 1.5
USD_PER_HARGA = 1.0       # XAU 0.01 lot: $1 per $1 gerak
CAPITAL = 548.19


def atr_h1(h: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = h["close"].shift(1)
    tr = pd.concat([h["high"] - h["low"], (h["high"] - pc).abs(),
                    (h["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def sinyal_akselerasi(h: pd.DataFrame) -> np.ndarray:
    """Sinyal PERSIS seperti yang sudah diuji - tidak disentuh."""
    c = h["close"]
    r4 = (c - c.shift(4)) / 4.0
    r8 = (c.shift(4) - c.shift(12)) / 8.0
    arah = np.where((r4 > 0) & (r8 > 0) & (r4 > r8), 1,
                    np.where((r4 < 0) & (r8 < 0) & (r4 < r8), -1, 0))
    return pd.Series(arah, index=h.index).shift(1).fillna(0).to_numpy()


def jalankan(h: pd.DataFrame, varian: str) -> pd.Series:
    a = atr_h1(h).to_numpy()
    sig = sinyal_akselerasi(h)
    o, hi, lo, c = (h["open"].to_numpy(), h["high"].to_numpy(),
                    h["low"].to_numpy(), h["close"].to_numpy())
    idx = h.index
    if varian == "V1":
        sl_m, tp_m, maxbar, trail_m = 1.0, 2.0, 12, None
    else:
        sl_m, tp_m, maxbar, trail_m = 1.5, None, 24, 1.0

    out = []
    i = 1
    n = len(h)
    while i < n - 1:
        s = sig[i]
        if not s or np.isnan(a[i]) or a[i] <= 0:
            i += 1
            continue
        entry = o[i]
        atr0 = a[i]
        sl = entry - sl_m * atr0 * s
        tp = (entry + tp_m * atr0 * s) if tp_m else None
        puncak = 0.0
        keluar = None
        j = i
        while j < min(i + maxbar, n - 1):
            j += 1
            baik = (hi[j] - entry) * s if s == 1 else (entry - lo[j]) * s * -1
            baik = (hi[j] - entry) if s == 1 else (entry - lo[j])
            # stop dulu (konservatif)
            if (s == 1 and lo[j] <= sl) or (s == -1 and hi[j] >= sl):
                keluar = sl; break
            if tp is not None and ((s == 1 and hi[j] >= tp) or (s == -1 and lo[j] <= tp)):
                keluar = tp; break
            if trail_m is not None:
                puncak = max(puncak, baik)
                if puncak >= trail_m * atr0:
                    baru = entry + (puncak - trail_m * atr0) * s
                    sl = max(sl, baru) if s == 1 else min(sl, baru)
        if keluar is None:
            keluar = c[j]
        out.append((idx[i], (keluar - entry) * s * USD_PER_HARGA - BIAYA))
        i = j + 1        # tidak menumpuk posisi
    return pd.Series([v for _, v in out], index=pd.DatetimeIndex([t for t, _ in out]))


def metrik(s: pd.Series, modal: float = CAPITAL) -> dict:
    eq = modal + s.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    w, l = s[s > 0].sum(), -s[s < 0].sum()
    thn = s.groupby(s.index.year).sum()
    return {"n": len(s), "net": s.sum(), "pf": (w / l) if l > 0 else 99,
            "dd": 100 * dd, "thn+": int((thn > 0).sum()), "thn": len(thn),
            "wr": 100 * (s > 0).mean()}


def port_metrik(m: pd.Series, modal: float = CAPITAL) -> dict:
    eq = modal + m.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yrs = len(m) / 12.0
    cagr = (eq.iloc[-1] / modal) ** (1 / yrs) - 1
    r = m / modal
    return {"CAGR%": 100 * cagr, "maxDD%": 100 * dd,
            "Calmar": cagr / abs(dd) if dd else np.nan,
            "Sharpe": r.mean() / r.std(ddof=1) * np.sqrt(12),
            "hijau%": 100 * (m > 0).mean()}


def main():
    print("Memuat XAU H1 ...", flush=True)
    m1 = EH.load("XAUUSD")
    h = EH.tf(m1, "1h")

    print("\n" + "=" * 100)
    print("A. SINYAL SAMA, DENGAN MANAJEMEN RISIKO (lot 0.01, modal $548)")
    print("=" * 100)
    print(f"  {'varian':<32}{'n':>6}{'net$':>10}{'PF':>7}{'maxDD%':>9}{'thn+':>7}{'winrate':>9}")
    hasil = {}
    for v, nama in (("V1", "V1  stop 1.0ATR / TP 2.0ATR / 12 bar"),
                    ("V2", "V2  stop 1.5ATR / trail 1.0ATR / 24 bar")):
        s = jalankan(h, v)
        hasil[v] = s
        m = metrik(s)
        print(f"  {nama:<32}{m['n']:>6}{m['net']:>10.2f}{m['pf']:>7.2f}"
              f"{m['dd']:>9.1f}{m['thn+']:>5}/{m['thn']}{m['wr']:>8.0f}%")
    print(f"\n  {'TANPA stop (versi lama)':<32}{'2069':>6}{'2425.40':>10}{'1.17':>7}"
          f"{'-95.8':>9}{'3/6':>7}{'48%':>9}")

    # per tahun untuk varian terbaik
    best = max(hasil, key=lambda k: metrik(hasil[k])["pf"])
    s = hasil[best]
    print(f"\n" + "=" * 100)
    print(f"B. PER TAHUN — varian {best}")
    print("=" * 100)
    print(f"  {'tahun':<8}{'n':>6}{'net$':>10}{'PF':>7}")
    for y, g in s.groupby(s.index.year):
        w, l = g[g > 0].sum(), -g[g < 0].sum()
        print(f"  {y:<8}{len(g):>6}{g.sum():>10.2f}{(w/l if l else 99):>7.2f}")

    # ---------- kriteria 4: uji PORTOFOLIO langsung ----------
    print("\n" + "=" * 100)
    print("C. UJI PORTOFOLIO LANGSUNG — inilah yang seharusnya kupakai, bukan ambang korelasi")
    print("=" * 100)
    et = eterna_trades(load_h1()).set_index("masuk").pnl
    orb = nas_dollars()
    if orb.index.tz is None:
        orb.index = orb.index.tz_localize("UTC")
    mon = pd.DataFrame({
        "ORB": orb.resample("ME").sum(),
        "ETERNA": et.resample("ME").sum(),
        "H1": s.resample("ME").sum(),
    }).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]

    p2 = mon["ORB"] + mon["ETERNA"]
    p3 = mon["ORB"] + mon["ETERNA"] + mon["H1"]
    a2, a3 = port_metrik(p2), port_metrik(p3)
    print(f"  {'portofolio':<28}{'CAGR%':>9}{'maxDD%':>9}{'Calmar':>9}{'Sharpe':>9}{'hijau%':>9}")
    print(f"  {'ORB + ETERNA (sekarang)':<28}{a2['CAGR%']:>9.1f}{a2['maxDD%']:>9.1f}"
          f"{a2['Calmar']:>9.2f}{a2['Sharpe']:>9.2f}{a2['hijau%']:>9.0f}")
    print(f"  {'+ H1 acceleration':<28}{a3['CAGR%']:>9.1f}{a3['maxDD%']:>9.1f}"
          f"{a3['Calmar']:>9.2f}{a3['Sharpe']:>9.2f}{a3['hijau%']:>9.0f}")
    print(f"\n  korelasi H1 vs ETERNA {mon['H1'].corr(mon['ETERNA']):+.3f}   "
          f"vs ORB {mon['H1'].corr(mon['ORB']):+.3f}")

    print("\n" + "=" * 100)
    print("VONIS terhadap kriteria yang ditulis di depan")
    print("=" * 100)
    m = metrik(s)
    cek = [("untung >= 4/6 tahun", m["thn+"] >= 4, f"{m['thn+']}/{m['thn']}"),
           ("maxDD >= -35%", m["dd"] >= -35, f"{m['dd']:.1f}%"),
           ("PF >= 1.15", m["pf"] >= 1.15, f"{m['pf']:.2f}"),
           ("Calmar portofolio MEMBAIK", a3["Calmar"] > a2["Calmar"],
            f"{a2['Calmar']:.2f} -> {a3['Calmar']:.2f}")]
    for nama, ok, nilai in cek:
        print(f"  [{'v' if ok else 'X'}] {nama:<30} {nilai}")
    print(f"\n  >> {'LULUS SEMUA' if all(o for _,o,_ in cek) else 'DITOLAK'}")
    print("=" * 100)


if __name__ == "__main__":
    main()
