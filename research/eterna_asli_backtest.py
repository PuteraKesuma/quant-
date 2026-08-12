"""ETERNA_ASLI — backtest yang SEHARUSNYA dilakukan sebelum dipasang.

DUDUK PERKARANYA:
Slot `eterna_asli` (magic 920641) dipasang 2026-08-10 sebagai REKONSTRUKSI dari riwayat
deal user, bukan dari hasil uji. Dia dibangun dengan membaca 3 trade di MT5 lama
(28 Jul - 5 Agu 2026, net +$58,25) lalu menebak sisanya. Sampai hari ini konfigurasinya
BELUM PERNAH di-backtest lintas tahun sama sekali.

Live sejak dipasang: 3 trade, 3 RUGI, -$29,29 (semuanya kena SL manual $10).
Gabungan dengan riwayat asli: 6 trade, net +$28,96. Sampel sebesar itu tidak berarti
apa-apa - makanya perlu backtest.

KONFIGURASI YANG DIUJI (persis dari config.yaml):
    timeframe 4h | atr_period 10 | mult_entry 1.2
    mode "direct"      -> always-in stop-and-reverse, TANPA gate tren
    sl_mode "manual"   -> SL jarak tetap $10 (= 1000 poin x 0.01 lot)
    tp_ratio 0         -> TANPA TP; keluar lewat sinyal balik atau SL
    min_sl_dist 0.30

Bentuk pembayarannya: banyak rugi kecil -$10, sesekali menang besar dari menunggangi
tren sampai sinyal balik. Riwayat asli user memperlihatkan pola itu persis:
-0,34 / +68,59 / -10,00.

TIMEFRAME 4h ADALAH TEBAKAN - dipilih dari kecocokan frekuensi trade, tidak pernah
diverifikasi. Jadi M30/H1/H4/D1 semuanya diuji dan DILAPORKAN APA ADANYA. Ini bukan
penyapuan untuk mencari yang terbaik; ini usaha mengidentifikasi apa yang sebenarnya
user jalankan. Kalau hanya satu timeframe yang untung, itu justru tanda bahaya.

Swap dimodelkan (LONG -$0,6995/malam, SHORT +$0,2491, Rabu 3x) - pelajaran hari ini.

Jalankan: python research/eterna_asli_backtest.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))
from blocking_akurat import supertrend

LOT, SPREAD = 0.01, 0.50
MANUAL_SL = 10.0          # $ pada 0.01 lot = gerak emas $10
ATR_P, MULT = 10, 1.2
MIN_SL = 0.30
SWAP_LONG, SWAP_SHORT = -0.6995, 0.2491
CAPITAL = 548.19


def load_m1():
    con = duckdb.connect(str(ROOT / "data" / "Level_0_Raw" / "XAUUSD_1m.duckdb"), read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def tf(m1, rule):
    return m1.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def malam(masuk, keluar):
    tot, d = 0.0, masuk.normalize()
    while d < keluar.normalize():
        d += pd.Timedelta(days=1)
        if d > keluar.normalize():
            break
        wd = d.weekday()
        if wd in (5, 6):
            continue
        tot += 3.0 if wd == 2 else 1.0
    return tot


def jalankan(h: pd.DataFrame) -> pd.DataFrame:
    """mode direct: always-in stop-and-reverse pada flip Supertrend, SL manual, tanpa TP."""
    st = supertrend(h, ATR_P, MULT)
    flip = st.where(st != st.shift(1)).shift(1).to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    idx = h.index

    pos = 0; entry = sl = 0.0; ei = 0
    out = []
    for i in range(1, len(h)):
        if pos != 0:
            kena = (lo[i] <= sl) if pos == 1 else (hi[i] >= sl)
            if kena:
                out.append((idx[ei], idx[i], pos, entry, sl, "SL"))
                pos = 0
        s = flip[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:                       # sinyal balik -> tutup lalu balik arah
            out.append((idx[ei], idx[i], pos, entry, o[i], "BALIK"))
            pos = 0
        if pos != 0:
            continue
        dist = MANUAL_SL
        if dist < MIN_SL:
            continue
        pos, entry, ei = s, o[i], i
        sl = o[i] - dist if s == 1 else o[i] + dist

    t = pd.DataFrame(out, columns=["masuk", "keluar", "arah", "px_in", "px_out", "sebab"])
    if len(t) == 0:
        return t
    t["kotor"] = (t.px_out - t.px_in) * t.arah * LOT * 100
    t["malam"] = [malam(a, b) for a, b in zip(t.masuk, t.keluar)]
    t["swap"] = np.where(t.arah == 1, t.malam * SWAP_LONG, t.malam * SWAP_SHORT)
    t["pnl"] = t.kotor - SPREAD + t.swap
    return t


def ringkas(t, label):
    if len(t) < 20:
        return {"timeframe": label, "n": len(t), "net$": 0, "PF": 0, "winrate%": 0,
                "maxDD%": 0, "thn+": "-", "menang terbesar$": 0}
    d = t.set_index("masuk").pnl
    eq = CAPITAL + d.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    w, l = d[d > 0].sum(), -d[d < 0].sum()
    thn = d.groupby(d.index.year).sum()
    return {"timeframe": label, "n": len(d), "net$": round(d.sum(), 2),
            "PF": round((w / l) if l > 0 else 99, 2),
            "winrate%": round(100 * (d > 0).mean()),
            "maxDD%": round(100 * dd, 1),
            "thn+": f"{int((thn>0).sum())}/{len(thn)}",
            "menang terbesar$": round(d.max(), 2)}


def main():
    print("Membangun ...", flush=True)
    m1 = load_m1()

    print("\n" + "=" * 108)
    print("A. KONFIGURASI ETERNA_ASLI DI BERBAGAI TIMEFRAME (4h yang terpasang adalah TEBAKAN)")
    print("=" * 108)
    hasil = {}
    rows = []
    for rule, lab in (("30min", "M30"), ("1h", "H1"), ("4h", "H4  <- terpasang"), ("1D", "D1")):
        t = jalankan(tf(m1, rule))
        hasil[lab] = t
        rows.append(ringkas(t, lab))
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n" + "=" * 108)
    print("B. H4 (yang benar-benar dipasang) — rincian per tahun")
    print("=" * 108)
    t = hasil["H4  <- terpasang"]
    t["thn"] = t.masuk.dt.year
    print(f"  {'tahun':<8}{'n':>6}{'net$':>10}{'PF':>7}{'winrate':>9}{'terbesar$':>12}")
    for y, g in t.groupby("thn"):
        w, l = g.pnl[g.pnl > 0].sum(), -g.pnl[g.pnl < 0].sum()
        print(f"  {y:<8}{len(g):>6}{g.pnl.sum():>10.2f}{(w/l if l else 99):>7.2f}"
              f"{100*(g.pnl>0).mean():>8.0f}%{g.pnl.max():>12.2f}")

    print(f"\n  sebab keluar: " + ", ".join(f"{k} {v}" for k, v in t.sebab.value_counts().items()))
    kalah = t.pnl[t.pnl < 0]
    menang = t.pnl[t.pnl > 0]
    print(f"  rata-rata rugi  ${kalah.mean():.2f}   rata-rata menang ${menang.mean():.2f}")
    if len(menang) and len(kalah):
        impas = 100 * abs(kalah.mean()) / (menang.mean() + abs(kalah.mean()))
        print(f"  winrate IMPAS yang dibutuhkan: {impas:.1f}%   winrate NYATA: "
              f"{100*(t.pnl>0).mean():.1f}%   margin {100*(t.pnl>0).mean()-impas:+.1f}")

    print("\n" + "=" * 108)
    print("C. PEMBANDING: eterna_xau versi riset (H1, conservative, TP 1:4, SL struktur)")
    print("=" * 108)
    from blocking_akurat import eterna_trades, load_h1 as lh
    e = eterna_trades(lh())
    e["malam"] = [malam(a, b) for a, b in zip(e.masuk, e.keluar)]
    e["pnl2"] = e.pnl + np.where(e.arah == 1, e.malam * SWAP_LONG, e.malam * SWAP_SHORT)
    d = e.set_index("masuk").pnl2
    w, l = d[d > 0].sum(), -d[d < 0].sum()
    thn = d.groupby(d.index.year).sum()
    eq = CAPITAL + d.cumsum()
    print(f"  eterna_xau (riset)  n={len(d)}  net ${d.sum():.2f}  PF {w/l:.2f}  "
          f"winrate {100*(d>0).mean():.0f}%  maxDD {100*float(((eq-eq.cummax())/eq.cummax()).min()):.1f}%  "
          f"thn+ {int((thn>0).sum())}/{len(thn)}")

    print("\n" + "=" * 108)


if __name__ == "__main__":
    main()
