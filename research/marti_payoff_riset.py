"""OPSI B: cari struktur TP/SL yang masuk akal untuk sinyal 'Semi Marti Cuan v10'.

DUDUK PERKARANYA (dari uji MT5 Strategy Tester, bukan dugaan):
  2026 saja  : +$236.85  PF 1.80  winrate 89.8%  -> untung
  2021-2025  : -$999.49  PF 0.75  winrate 79.9%  -> MODAL HABIS, DD 99.95%

  rata-rata menang +$5.05, rata-rata rugi -$26.91
  -> winrate impas = 26.91 / (5.05 + 26.91) = 84.2%
  2026 kebetulan 89.8% (di atas ambang), 5 tahun 79.9% (di bawah) -> ludes.

Jadi yang rusak BUKAN sinyalnya. Winrate 80% selama 5 tahun berarti sinyal fade-nya
memang menangkap sesuatu. Yang rusak adalah PEMBUNGKUSNYA: ambil untung $10 sambil
membiarkan rugi sampai $70. Rasio itu menuntut winrate yang tidak realistis.

Skrip ini membuang seluruh pembungkus (dual entry, trailing, martingale, target harian)
dan menanyakan satu hal saja: DENGAN SINYAL YANG SAMA, struktur TP/SL mana yang punya
ekspektasi positif LINTAS TAHUN, bukan cuma 2026?

Sinyalnya memakai research/marti_signal_port.py yang SUDAH DIVALIDASI terhadap tester
(47 vs 49 sinyal di 2026, meleset 4%). Jangan pakai skrip ini kalau validasi itu rusak.

Jalankan: python research/marti_payoff_riset.py
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
from marti_signal_port import build_signals, SRV_OFFSET_H

LOT = 0.01
USD_PER_DOLAR = 1.0        # XAU 0.01 lot: gerak $1 emas = $1 P/L
SPREAD_USD = 0.30          # biaya bolak-balik pada 0.01 lot (~30 sen emas)
CAPITAL = 1000.0


def load_m15_duckdb() -> pd.DataFrame:
    con = duckdb.connect(str(ROOT / "data" / "Level_0_Raw" / "XAUUSD_1m.duckdb"), read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def to_m15(m1: pd.DataFrame) -> pd.DataFrame:
    return m1.resample("15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def simulasi(m1: pd.DataFrame, sinyal: pd.Series, tp_usd: float, sl_usd: float,
             max_jam: int = 0) -> pd.DataFrame:
    """Satu posisi 0.01 lot per sinyal. Jalan di bar M1 supaya urutan SL/TP jujur.

    SL DICEK SEBELUM TP dalam satu bar - asumsi konservatif; kalau satu bar M1
    menyentuh keduanya kita anggap yang buruk duluan.
    """
    o = m1["open"].to_numpy(); hi = m1["high"].to_numpy()
    lo = m1["low"].to_numpy(); idx = m1.index
    pos_of = {t: i for i, t in enumerate(idx)}

    keluar = []
    for ts, arah in sinyal.items():
        # entry di OPEN bar M1 berikutnya setelah bar sinyal M15 tertutup
        t_entry = ts + pd.Timedelta(minutes=15)
        i = pos_of.get(t_entry)
        if i is None:
            nxt = idx.searchsorted(t_entry)
            if nxt >= len(idx):
                continue
            i = int(nxt)
        entry = o[i]
        tp = entry + tp_usd * arah
        sl = entry - sl_usd * arah
        batas = i + (max_jam * 60 if max_jam else 60 * 24 * 10)
        batas = min(batas, len(m1) - 1)

        hasil = None
        for j in range(i, batas + 1):
            if arah == 1:
                if lo[j] <= sl: hasil = (-sl_usd, idx[j], "SL"); break
                if hi[j] >= tp: hasil = (tp_usd, idx[j], "TP"); break
            else:
                if hi[j] >= sl: hasil = (-sl_usd, idx[j], "SL"); break
                if lo[j] <= tp: hasil = (tp_usd, idx[j], "TP"); break
        if hasil is None:
            px = m1["close"].to_numpy()[batas]
            hasil = ((px - entry) * arah, idx[batas], "WAKTU")
        keluar.append((ts, hasil[1], arah, entry, hasil[0] - SPREAD_USD, hasil[2]))

    return pd.DataFrame(keluar, columns=["masuk", "keluar", "arah", "px", "pnl", "sebab"])


def lapor(t: pd.DataFrame, label: str) -> dict | None:
    if len(t) < 20:
        return None
    d = t.set_index("masuk").pnl
    eq = CAPITAL + d.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    menang = d[d > 0]; kalah = d[d <= 0]
    pf = menang.sum() / -kalah.sum() if len(kalah) and kalah.sum() < 0 else np.inf
    wr = 100 * len(menang) / len(d)
    aw = menang.mean() if len(menang) else 0.0
    al = -kalah.mean() if len(kalah) else 0.0
    impas = 100 * al / (aw + al) if (aw + al) > 0 else np.nan
    return {"struktur": label, "n": len(d), "net$": round(d.sum(), 2),
            "PF": round(pf, 2), "winrate%": round(wr, 1),
            "impas%": round(impas, 1), "margin": round(wr - impas, 1),
            "maxDD%": round(100 * dd, 1)}


def main():
    print("Memuat data & membangun sinyal ...", flush=True)
    m1 = load_m15_duckdb()
    m15 = to_m15(m1)
    sg = build_signals(m15)
    s = sg[sg["sinyal"] != 0]["sinyal"]
    print(f"  bar M1 {len(m1):,}   bar M15 {len(m15):,}   sinyal {len(s)}")
    print(f"  rentang {s.index[0]:%Y-%m-%d} .. {s.index[-1]:%Y-%m-%d}")
    s26 = s[s.index >= pd.Timestamp('2026-01-01', tz='UTC')]
    print(f"  sinyal 2026 (acuan tester 49, port MT5 47): {len(s26)}")

    print("\n" + "=" * 104)
    print("A. SEBERAPA JAUH HARGA BERGERAK SETELAH SINYAL - ini yang menentukan TP/SL mungkin")
    print("=" * 104)
    t = simulasi(m1, s, 999.0, 999.0, max_jam=48)   # tanpa TP/SL efektif: ukur jalurnya
    o = m1["open"].to_numpy(); hi = m1["high"].to_numpy(); lo = m1["low"].to_numpy()
    pos_of = {ts: i for i, ts in enumerate(m1.index)}
    mfe, mae = [], []
    for ts, arah in s.items():
        i = pos_of.get(ts + pd.Timedelta(minutes=15))
        if i is None:
            continue
        j = min(i + 48 * 60, len(m1) - 1)
        e = o[i]
        if arah == 1:
            mfe.append(hi[i:j].max() - e); mae.append(e - lo[i:j].min())
        else:
            mfe.append(e - lo[i:j].min()); mae.append(hi[i:j].max() - e)
    mfe = np.array(mfe); mae = np.array(mae)
    print(f"  dalam 48 jam setelah entry (satuan dolar emas, = $ P/L pada 0.01 lot):")
    print(f"    {'persentil':<12}{'MFE (arah benar)':>20}{'MAE (arah salah)':>20}")
    for q in (25, 50, 75, 90):
        print(f"    {q:>3}%{'':<8}{np.percentile(mfe,q):>20.2f}{np.percentile(mae,q):>20.2f}")
    print(f"\n  Bacaan: TP harus di bawah MFE tipikal, SL di atas MAE tipikal. Struktur asli")
    print(f"  EA (TP $10 / SL $70) memilih TP JAUH di bawah MFE median dan SL jauh di atas")
    print(f"  MAE median - persis resep 'menang kecil, kalah besar'.")

    print("\n" + "=" * 104)
    print("B. SAPUAN TP/SL - satu posisi 0.01, tanpa martingale, tanpa dual entry")
    print("=" * 104)
    rows = []
    for tp in (5, 8, 10, 15, 20, 30):
        for sl in (5, 8, 10, 15, 20, 30):
            r = lapor(simulasi(m1, s, tp, sl, max_jam=48), f"TP ${tp} / SL ${sl}")
            if r:
                rows.append(r)
    df = pd.DataFrame(rows).sort_values("net$", ascending=False)
    print(df.head(14).to_string(index=False))
    print("\n  kolom 'impas%' = winrate minimum agar tidak rugi. 'margin' = winrate nyata")
    print("  dikurangi impas. Margin NEGATIF berarti ekspektasi negatif, sebagus apa pun")
    print("  winrate-nya terlihat. Inilah yang membunuh struktur asli EA.")

    print("\n" + "=" * 104)
    print("C. TIGA TERBAIK, DIUJI PER TAHUN - satu tahun bagus tidak cukup")
    print("=" * 104)
    for _, r in df.head(3).iterrows():
        tp = float(r["struktur"].split("$")[1].split(" ")[0])
        sl = float(r["struktur"].split("$")[2])
        tt = simulasi(m1, s, tp, sl, max_jam=48)
        tt["thn"] = tt.masuk.dt.year
        print(f"\n  {r['struktur']}   (net ${r['net$']}, PF {r['PF']}, margin {r['margin']})")
        print(f"    {'tahun':<8}{'n':>5}{'net$':>10}{'PF':>7}{'winrate':>10}")
        rugi = 0
        for y, g in tt.groupby("thn"):
            m_, k_ = g.pnl[g.pnl > 0], g.pnl[g.pnl <= 0]
            pf = m_.sum() / -k_.sum() if len(k_) and k_.sum() < 0 else 99
            if g.pnl.sum() < 0:
                rugi += 1
            print(f"    {y:<8}{len(g):>5}{g.pnl.sum():>10.2f}{pf:>7.2f}{100*(g.pnl>0).mean():>9.0f}%")
        print(f"    -> {rugi} dari {tt.thn.nunique()} tahun RUGI")

    print("\n" + "=" * 104)
    print("Pemenang di sini BELUM boleh dipasang. Dia harus dikonfirmasi ulang di MT5")
    print("Strategy Tester dengan data broker sebelum jadi sleeve.")
    print("=" * 104)


if __name__ == "__main__":
    main()
