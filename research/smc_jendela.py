"""Apakah panjang riwayat mengubah sinyal? Live cuma punya ~272 bar H4; backtest punya 8766.

Mesin BOS adalah state machine yang berjalan maju (last_swing + penanda 'sudah ditembus').
Kalau jendela riwayat live terlalu pendek, keadaan akhirnya bisa berbeda dari backtest ->
live memasang order yang tidak ada di backtest, atau melewatkan yang ada. Itu kelas
penyimpangan yang sama dengan 4 cacat paritas RSI2.

Uji: untuk tiap bar, hitung setup dengan riwayat PENUH vs jendela bergulir N bar.
Hitung berapa banyak yang tidak sepakat.
"""
from __future__ import annotations
import warnings, sys; warnings.filterwarnings("ignore")
from pathlib import Path
import pandas as pd
ROOT = Path(r"C:\Quant"); sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT/"research"))
from smc_xau_backtest import load_m1, tf
from pipeline.live.smc_limit_manager import SmcLimitManager

def mgr():
    m = SmcLimitManager.__new__(SmcLimitManager)
    m.timeframe="4h"; m.k=3; m.ob_lookback=10; m.expiry_bars=12
    m.rr=2.0; m.buffer_frac=0.10; m.use_fvg=True
    return m

h = tf(load_m1(), "4h"); m = mgr()
N_UJI = 900
mulai = len(h) - N_UJI
print("menguji %d bar terakhir (%s .. %s)\n" % (N_UJI, h.index[mulai].date(), h.index[-1].date()))

penuh = {}
for i in range(mulai, len(h)):
    s = m._setup_terkini(h.iloc[:i+1])
    penuh[i] = None if s is None else (s["arah"], round(s["price"],2))

print("%-10s %10s %10s %10s" % ("jendela", "beda", "% beda", "vonis"))
for W in (272, 400, 600, 900, 1500, 3000):
    beda = 0
    for i in range(mulai, len(h)):
        lo = max(0, i+1-W)
        s = m._setup_terkini(h.iloc[lo:i+1])
        got = None if s is None else (s["arah"], round(s["price"],2))
        if got != penuh[i]:
            beda += 1
    v = "IDENTIK" if beda == 0 else "MENYIMPANG"
    print("%-10d %10d %9.1f%% %10s" % (W, beda, 100*beda/N_UJI, v))
