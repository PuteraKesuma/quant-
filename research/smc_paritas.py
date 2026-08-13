"""PARITAS live vs backtest untuk SMC — apakah manager menghasilkan order yang SAMA?

Kenapa ini wajib: sleeve RSI2 punya EMPAT cacat paritas antara logika live dan
backtest-nya (memangkas -81%, termasuk bug re-entry yang menggandakan drawdown jadi
-49,4%). Cacat itu tidak pernah muncul di backtest maupun di log live secara terpisah;
hanya terlihat saat keduanya diadu langsung.

Uji ini SUDAH MENANGKAP satu cacat nyata: `_setup_terkini()` tidak mensimulasikan
keluar SL/TP, sehingga meng-arm 2 order hantu saat backtest masih memegang posisi.

CARA UJI:
  1. Jalankan backtest -> daftar trade beserta harga limit.
  2. Untuk tiap bar dalam jendela uji, panggil SmcLimitManager._setup_terkini() dengan
     riwayat DIPOTONG sampai bar itu (meniru apa yang manager lihat saat live).
  3. Cocokkan: tiap pending yang di-arm manager harus punya pasangan di backtest
     dengan arah dan harga limit identik sampai 2 desimal.
  4. Arah sebaliknya: pending manager yang harganya TERSENTUH tapi tidak ada di
     backtest = order hantu. (Pending yang kedaluwarsa tanpa tersentuh itu WAJAR.)

Jendela fill yang sah dimulai di bar SESUDAH bar BOS: pending baru ter-arm di
penutupan bar BOS, jadi low/high bar itu sendiri tidak bisa mengisinya.

Menguji KEDUA aliran live: H4-B (magic 920643) dan H1-C (magic 920644).

Jalankan: python research/smc_paritas.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))

from smc_xau_backtest import load_m1, tf, jalankan
from pipeline.live.smc_limit_manager import SmcLimitManager

N_BAR_UJI = 1200


def manager_tiruan(timeframe, use_fvg, use_sweep) -> SmcLimitManager:
    """Manager tanpa MT5 — hanya bagian penghitung sinyal yang dipakai."""
    m = SmcLimitManager.__new__(SmcLimitManager)
    m.timeframe = timeframe; m.k = 3; m.ob_lookback = 10; m.expiry_bars = 12
    m.rr = 2.0; m.buffer_frac = 0.10
    m.use_fvg = use_fvg; m.use_sweep = use_sweep; m.sweep_window = 5
    return m


def uji(nama, h, mgr, bt_kw) -> bool:
    bt = jalankan(h, k=3, ob_lookback=10, expiry=12, rr=2.0, buffer_frac=0.10, **bt_kw)
    print("\n" + "=" * 96)
    print(f"{nama}   backtest: {len(bt)} trade, net ${bt.pnl.sum():.2f}")
    print("=" * 96)

    mulai = max(0, len(h) - N_BAR_UJI)
    setups = {}
    for i in range(mulai, len(h)):
        s = mgr._setup_terkini(h.iloc[:i + 1])
        if s is not None and s["bos_time"] not in setups:
            setups[s["bos_time"]] = s
    bt_uji = bt[bt.masuk >= h.index[mulai]]
    print(f"  jendela {h.index[mulai]:%Y-%m-%d}..{h.index[-1]:%Y-%m-%d}   "
          f"manager arm {len(setups)} pending   backtest {len(bt_uji)} trade")

    harga_mgr = {round(s["price"], 2): s for s in setups.values()}
    cocok = beda = hilang = 0
    for _, r in bt_uji.iterrows():
        s = harga_mgr.get(round(float(r.px_in), 2))
        if s is None:
            hilang += 1
            if hilang <= 3:
                print(f"    HILANG {r.masuk:%Y-%m-%d %H:%M} arah {int(r.arah):+d} "
                      f"limit {round(float(r.px_in), 2)}")
        elif int(s["arah"]) != int(r.arah):
            beda += 1
            print(f"    ARAH BEDA {r.masuk:%Y-%m-%d %H:%M}")
        else:
            cocok += 1

    px_bt = {round(float(x), 2) for x in bt_uji.px_in}
    hantu = kedaluwarsa = 0
    for s in setups.values():
        if round(s["price"], 2) in px_bt:
            continue
        seg = h.loc[s["bos_time"]:s["expiry_time"]].iloc[1:]
        if len(seg) < 1:
            continue
        tersentuh = (seg["low"].min() <= s["price"]) if s["arah"] == 1 \
            else (seg["high"].max() >= s["price"])
        if tersentuh:
            hantu += 1
            if hantu <= 3:
                print(f"    HANTU BOS {s['bos_time']:%Y-%m-%d %H:%M} "
                      f"arah {s['arah']:+d} limit {round(s['price'], 2)}")
        else:
            kedaluwarsa += 1

    lulus = (hilang == 0 and beda == 0 and hantu == 0 and cocok == len(bt_uji))
    print(f"  cocok {cocok}/{len(bt_uji)}   arah beda {beda}   hilang {hilang}   "
          f"hantu {hantu}   kedaluwarsa wajar {kedaluwarsa}")
    print(f"  -> {'LULUS' if lulus else 'GAGAL'}")
    return lulus


def main():
    print("Membangun ...", flush=True)
    m1 = load_m1()
    hasil = []
    hasil.append(uji("H4-B (magic 920643) OB+BOS+FVG",
                     tf(m1, "4h"),
                     manager_tiruan("4h", True, False),
                     dict(pakai_fvg=True, pakai_sweep=False)))
    hasil.append(uji("H1-C (magic 920644) OB+BOS+SWEEP",
                     tf(m1, "1h"),
                     manager_tiruan("1h", False, True),
                     dict(pakai_fvg=False, pakai_sweep=True)))

    print("\n" + "=" * 96)
    semua = all(hasil)
    print("VONIS PARITAS: " + ("LULUS untuk KEDUA aliran - live akan menghasilkan order "
                               "yang sama dengan backtest"
                               if semua else
                               "GAGAL - ada aliran yang MENYIMPANG. JANGAN dipasang."))
    print("=" * 96)
    return 0 if semua else 1


if __name__ == "__main__":
    sys.exit(main())
