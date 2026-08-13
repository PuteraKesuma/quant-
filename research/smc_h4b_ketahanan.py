"""UJI KETAHANAN untuk satu-satunya kandidat SMC yang lolos: H4 config B (OB+BOS+FVG).

Dari 16 kombinasi yang diuji, hanya H4-B yang terlihat meyakinkan:
    n=96  net +$630.93  PF 1.81  maxDD -14.8%  5/6 tahun hijau  margin impas +13.5

Tapi DSR-nya 0.629 (ambang 0.95) dan tetangganya (H4-A, H4-C) rugi. Jadi pertanyaannya
BUKAN "berapa hasilnya" melainkan "apakah ini dataran atau paku tunggal".

INI BUKAN PENYAPUAN UNTUK MENCARI YANG LEBIH BAIK. Kalau ada tetangga parameter yang
lebih untung, itu TIDAK dipakai - justru memperkuat kesimpulan bahwa permukaannya
bergerigi dan angkanya kebetulan. Yang dicari: apakah hasil tetap positif di SEKITAR
parameter itu.

Empat uji:
  1. per tahun + kontribusi trade terbesar  -> apakah untungnya terkonsentrasi?
  2. tetangga parameter (k, expiry, rr, ob_lookback) -> dataran atau paku?
  3. beli-dan-tahan yang JUJUR (pakai swap + maxDD) -> pembanding apple-to-apple
  4. belah waktu: 2021-2023 (tentukan) vs 2024-2026 (nilai) -> masih hidup di data baru?

Jalankan: python research/smc_h4b_ketahanan.py
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
from smc_xau_backtest import (load_m1, tf, jalankan, malam, LOT,
                              SWAP_LONG, SWAP_SHORT, CAPITAL)

BASE = dict(k=3, ob_lookback=10, expiry=12, rr=2.0, buffer_frac=0.10,
            pakai_fvg=True, pakai_sweep=False)


def metrik(t: pd.DataFrame) -> dict:
    if len(t) < 10:
        return {"n": len(t), "net$": 0.0, "PF": 0.0, "maxDD%": 0.0}
    d = t.set_index("masuk").pnl
    eq = CAPITAL + d.cumsum()
    w, l = d[d > 0].sum(), -d[d < 0].sum()
    return {"n": len(d), "net$": round(d.sum(), 2),
            "PF": round(w / l if l > 0 else 99, 2),
            "maxDD%": round(100 * float(((eq - eq.cummax()) / eq.cummax()).min()), 1)}


def main():
    print("Membangun ...", flush=True)
    m1 = load_m1()
    h4 = tf(m1, "4h")
    t = jalankan(h4, **BASE)
    print(f"  acuan H4-B: n={len(t)}  net ${t.pnl.sum():.2f}   (harus 96 / +630.93)")

    # ---------------------------------------------------------------- 1
    print("\n" + "=" * 100)
    print("1. KONSENTRASI — apakah untungnya bergantung pada segelintir trade?")
    print("=" * 100)
    t["thn"] = t.masuk.dt.year
    print(f"  {'tahun':<8}{'n':>5}{'net$':>11}{'PF':>7}")
    for y, g in t.groupby("thn"):
        w, l = g.pnl[g.pnl > 0].sum(), -g.pnl[g.pnl < 0].sum()
        print(f"  {y:<8}{len(g):>5}{g.pnl.sum():>11.2f}{(w/l if l else 99):>7.2f}")

    d = t.pnl.sort_values(ascending=False)
    tot = d.sum()
    print(f"\n  net total ${tot:.2f}")
    for n_buang in (1, 3, 5):
        sisa = d.iloc[n_buang:].sum()
        print(f"  buang {n_buang} trade terbaik -> ${sisa:>8.2f}  "
              f"({100*sisa/tot:>5.1f}% dari total tersisa)")
    print(f"  trade terbesar ${d.iloc[0]:.2f} = {100*d.iloc[0]/tot:.0f}% dari seluruh net")

    # ---------------------------------------------------------------- 2
    print("\n" + "=" * 100)
    print("2. TETANGGA PARAMETER — dataran atau paku tunggal?")
    print("=" * 100)
    print("   (mencari KESTABILAN, bukan angka terbaik. Tetangga yang lebih untung")
    print("    justru pertanda buruk: berarti permukaannya bergerigi.)")
    for nama, nilai in (("k", [2, 3, 4, 5]),
                        ("expiry", [6, 9, 12, 18, 24]),
                        ("rr", [1.5, 2.0, 2.5, 3.0]),
                        ("ob_lookback", [6, 8, 10, 14, 20]),
                        ("buffer_frac", [0.0, 0.05, 0.10, 0.20])):
        print(f"\n  {nama}:")
        for v in nilai:
            kw = dict(BASE); kw[nama] = v
            m = metrik(jalankan(h4, **kw))
            tanda = "  <- acuan" if v == BASE[nama] else ""
            print(f"    {nama}={v:<6} n={m['n']:>4}  net ${m['net$']:>9.2f}  "
                  f"PF {m['PF']:>5.2f}  maxDD {m['maxDD%']:>6.1f}%{tanda}")

    # ---------------------------------------------------------------- 3
    print("\n" + "=" * 100)
    print("3. BELI-DAN-TAHAN YANG JUJUR (swap dimodelkan, maxDD dihitung)")
    print("=" * 100)
    c = m1["close"]
    kotor = (c.iloc[-1] - c.iloc[0]) * LOT * 100
    n_malam = malam(c.index[0], c.index[-1])
    swap_bh = n_malam * SWAP_LONG
    d1 = m1["close"].resample("1D").last().dropna()
    eq_bh = CAPITAL + (d1 - d1.iloc[0]) * LOT * 100
    dd_bh = float(((eq_bh - eq_bh.cummax()) / eq_bh.cummax()).min())
    print(f"  kotor                     ${kotor:>10.2f}")
    print(f"  swap ({n_malam:.0f} malam x -$0.6995) ${swap_bh:>10.2f}")
    print(f"  BERSIH                    ${kotor + swap_bh:>10.2f}   maxDD {100*dd_bh:.1f}%")
    print(f"\n  H4-B                      ${t.pnl.sum():>10.2f}   maxDD -14.8%")
    print("  >> beli-dan-tahan menang telak dalam untung; H4-B menang dalam drawdown.")

    # ---------------------------------------------------------------- 4
    print("\n" + "=" * 100)
    print("4. BELAH WAKTU — 2021-2023 (tentukan) vs 2024-2026 (nilai, belum tersentuh)")
    print("=" * 100)
    a = t[t.masuk < "2024-01-01"]
    b = t[t.masuk >= "2024-01-01"]
    for lab, g in (("2021-2023", a), ("2024-2026", b)):
        if len(g) < 5:
            print(f"  {lab}: n={len(g)} terlalu sedikit"); continue
        w, l = g.pnl[g.pnl > 0].sum(), -g.pnl[g.pnl < 0].sum()
        print(f"  {lab}: n={len(g):>3}  net ${g.pnl.sum():>8.2f}  "
              f"PF {(w/l if l else 99):>5.2f}  winrate {100*(g.pnl>0).mean():.0f}%")

    print("\n" + "=" * 100)


if __name__ == "__main__":
    main()
