"""PARITAS live vs backtest untuk SMC — apakah manager menghasilkan order yang SAMA?

Kenapa ini wajib: sleeve RSI2 punya EMPAT cacat paritas antara logika live dan
backtest-nya (memangkas -81%, termasuk bug re-entry yang menggandakan drawdown jadi
-49,4%). Cacat itu tidak pernah muncul di backtest maupun di log live secara terpisah;
hanya terlihat saat keduanya diadu langsung.

CARA UJI:
  1. Jalankan backtest research/smc_xau_backtest.py -> daftar trade beserta harga limit.
  2. Untuk tiap bar dalam jendela uji, panggil SmcLimitManager._setup_terkini() dengan
     riwayat yang DIPOTONG sampai bar itu (meniru apa yang manager lihat saat live).
  3. Cocokkan: setiap pending yang di-arm manager harus punya pasangan di backtest
     dengan arah, harga limit, SL, dan TP yang identik.

Yang dicari BUKAN "mirip" tapi "sama sampai 2 desimal". Selisih sekecil apa pun
berarti live akan menyimpang dari angka yang dipakai untuk memutuskan.

Jalankan: python research/smc_paritas.py
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

from smc_xau_backtest import load_m1, tf, jalankan
from pipeline.live.smc_limit_manager import SmcLimitManager

PARAMS = dict(k=3, ob_lookback=10, expiry=12, rr=2.0, buffer_frac=0.10,
              pakai_fvg=True, pakai_sweep=False)
N_BAR_UJI = 1200          # ~2 tahun H4; cukup untuk menangkap cacat sistematis


def manager_tiruan() -> SmcLimitManager:
    """Manager tanpa MT5 — hanya bagian penghitung sinyal yang dipakai."""
    m = SmcLimitManager.__new__(SmcLimitManager)
    m.timeframe = "4h"; m.k = 3; m.ob_lookback = 10; m.expiry_bars = 12
    m.rr = 2.0; m.buffer_frac = 0.10; m.use_fvg = True
    return m


def main():
    print("Membangun ...", flush=True)
    m1 = load_m1()
    h = tf(m1, "4h")
    bt = jalankan(h, **PARAMS)
    print(f"  backtest: {len(bt)} trade, net ${bt.pnl.sum():.2f}")

    mgr = manager_tiruan()
    mulai = len(h) - N_BAR_UJI
    print(f"  menelusuri {N_BAR_UJI} bar dengan riwayat dipotong "
          f"({h.index[mulai]:%Y-%m-%d} .. {h.index[-1]:%Y-%m-%d}) ...", flush=True)

    setups = {}
    for i in range(mulai, len(h)):
        s = mgr._setup_terkini(h.iloc[:i + 1])
        if s is None:
            continue
        key = s["bos_time"]
        if key not in setups:
            setups[key] = s
    print(f"  manager meng-arm {len(setups)} pending unik")

    # trade backtest yang masuk di dalam jendela uji
    awal_uji = h.index[mulai]
    bt_uji = bt[bt.masuk >= awal_uji]
    print(f"  backtest punya {len(bt_uji)} trade masuk di jendela yang sama")

    print("\n" + "=" * 96)
    print("COCOKKAN: tiap trade backtest harus punya pending manager dengan HARGA IDENTIK")
    print("=" * 96)
    harga_mgr = {round(s["price"], 2): s for s in setups.values()}
    cocok = beda = hilang = 0
    for _, r in bt_uji.iterrows():
        px = round(float(r.px_in), 2)
        s = harga_mgr.get(px)
        if s is None:
            hilang += 1
            if hilang <= 5:
                print(f"  HILANG  entry {r.masuk:%Y-%m-%d %H:%M} arah {int(r.arah):+d} "
                      f"limit {px} -> manager tidak pernah meng-arm harga ini")
            continue
        if int(s["arah"]) != int(r.arah):
            beda += 1
            print(f"  ARAH BEDA {r.masuk:%Y-%m-%d %H:%M} bt {int(r.arah):+d} vs mgr {s['arah']:+d}")
            continue
        cocok += 1
    print(f"\n  cocok {cocok}   arah beda {beda}   hilang {hilang}   dari {len(bt_uji)} trade")

    print("\n" + "=" * 96)
    print("ARAH SEBALIKNYA: pending manager yang TIDAK ada di backtest (order hantu)")
    print("=" * 96)
    px_bt = set(round(float(x), 2) for x in bt_uji.px_in)
    # pending yang kedaluwarsa memang TIDAK jadi trade -> itu WAJAR, bukan hantu.
    # Yang dihitung hantu: pending yang HARUSNYA terisi (harga tersentuh) tapi tak ada di backtest.
    hantu = 0
    for s in setups.values():
        px = round(s["price"], 2)
        if px in px_bt:
            continue
        # Jendela fill yang SAH dimulai di bar SESUDAH bar BOS: pending baru ter-arm
        # di penutupan bar BOS, jadi low/high bar itu sendiri tidak bisa mengisinya.
        # (Versi awal uji ini memasukkan bar BOS dan melaporkan 'hantu' palsu di
        #  2026-01-18: low bar BOS 4618.99 <= limit 4620.03, padahal 12 bar sesudahnya
        #  low terendah cuma 4652.85 -> order memang kedaluwarsa, backtest benar.)
        seg = h.loc[s["bos_time"]:s["expiry_time"]].iloc[1:]
        if len(seg) < 1:
            continue
        tersentuh = (seg["low"].min() <= s["price"]) if s["arah"] == 1 else (seg["high"].max() >= s["price"])
        if tersentuh:
            hantu += 1
            if hantu <= 5:
                print(f"  HANTU  BOS {s['bos_time']:%Y-%m-%d %H:%M} arah {s['arah']:+d} "
                      f"limit {px} tersentuh tapi tidak ada trade backtest")
    kedaluwarsa = len(setups) - len([1 for s in setups.values()
                                     if round(s['price'], 2) in px_bt]) - hantu
    print(f"\n  order hantu {hantu}   kedaluwarsa wajar {kedaluwarsa}")

    print("\n" + "=" * 96)
    vonis = (cocok == len(bt_uji) and beda == 0 and hilang == 0 and hantu == 0)
    print("VONIS PARITAS: " + ("LULUS - live akan menghasilkan order yang sama dengan backtest"
                               if vonis else
                               "GAGAL - live akan MENYIMPANG dari backtest. JANGAN dipasang."))
    print("=" * 96)
    return 0 if vonis else 1


if __name__ == "__main__":
    sys.exit(main())
