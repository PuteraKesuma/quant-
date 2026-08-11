"""ETERNA + konfirmasi support/resistance - SATU tes, kriteria ditetapkan DI DEPAN.

KENAPA HATI-HATI DI SINI:
Eterna sudah melewati ~1900 percobaan (24 fase riset) dan Deflated Sharpe-nya 0,0061 -
GAGAL. Menyapu ratusan kombinasi S/R lalu memilih yang terbaik akan MENAIKKAN N,
menaikkan ambang DSR, dan membuat bukti eterna makin lemah sambil angkanya terlihat
makin cantik. Itu persis cara sebuah strategi meyakinkan pemiliknya tepat sebelum gagal.

Jadi: TIGA hipotesis saja, ditetapkan sebelum melihat hasil, tanpa penyetelan lanjutan.
Kalau tidak ada yang lulus, jawabannya "tidak" dan eterna dibiarkan apa adanya.

KENAPA S/R MASUK AKAL DI SINI (bukan tempelan sembarangan):
Eterna memasang TP di 4x jarak stop. Kalau ada level yang menghalangi di tengah jalan
menuju TP itu, targetnya memang sulit tercapai. Menyaring entry yang jalannya terhalang
terkait LANGSUNG dengan cara eterna keluar - bukan indikator tambahan yang dicomot.

KRITERIA LULUS (ditulis sebelum skrip dijalankan, tidak boleh diubah sesudahnya):
  1. net 2021-2025 naik >= 25%
  2. jumlah trade tetap >= 60% dari dasar (kalau anjlok, dia cuma memetik pemenang)
  3. porsi bulan hijau tidak turun
  4. DSR dilaporkan apa adanya dengan N bertambah

KENAPA 2021-2025, BUKAN SELURUH PERIODE:
Di situ masalah eterna sebenarnya - 5 tahun cuma $726, CAGR 11,5%, 50% bulan hijau.
2026 sudah luar biasa (74% dari seluruh profit) tanpa bantuan apa pun. Filter yang cuma
memperbaiki 2026 adalah curve-fitting; yang memperbaiki 2021-2025 baru berarti.

ANTI-LOOKAHEAD - bagian yang paling gampang salah:
Pivot di bar i baru BISA DIKETAHUI di bar i+k (butuh k bar ke kanan untuk memastikan
dia pivot). Jadi saat memutuskan di bar t, hanya pivot dengan i <= t-k-1 yang boleh
dipakai. Bug ffill-tanpa-shift seperti ini yang dulu membunuh Golden (lookahead 55 menit).

Jalankan: python research/eterna_sr_konfirmasi.py
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
from blocking_akurat import load_h1, supertrend

LOT, COST, MIN_SL = 0.01, 0.50, 0.30
P, ME, MT, TPR = 16, 1.8, 3.8, 4.0        # parameter eterna terpasang - TIDAK disentuh
PIVOT_K = 5                                # bar kiri/kanan untuk memastikan pivot
PIVOT_LOOKBACK = 100                       # level dianggap relevan sejauh 100 bar
CAPITAL = 1000.0


def dsr(r, n_trials):
    r = np.asarray(r, float); n = len(r)
    if n < 12 or r.std(ddof=1) == 0:
        return np.nan, np.nan
    sr = r.mean() / r.std(ddof=1)
    sk, ku = stats.skew(r), stats.kurtosis(r, fisher=False)
    e = np.euler_gamma
    sr0 = np.sqrt(1.0 / (n - 1)) * ((1 - e) * stats.norm.ppf(1 - 1.0 / n_trials)
                                    + e * stats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    den = np.sqrt(1 - sk * sr + (ku - 1) / 4.0 * sr ** 2)
    if den <= 0 or np.isnan(den):
        return sr, np.nan
    return sr, stats.norm.cdf((sr - sr0) * np.sqrt(n - 1) / den)


def pivots(h: pd.DataFrame, k: int):
    """Pivot high/low yang SUDAH TERKONFIRMASI, plus bar kapan dia diketahui.

    Pivot di bar i butuh k bar di kanannya, jadi baru boleh dipakai sejak bar i+k.
    Kembalikan array `known_at` supaya penyaring tidak pernah melihat masa depan.
    """
    hi, lo = h["high"].to_numpy(), h["low"].to_numpy()
    n = len(h)
    ph = np.full(n, np.nan); pl = np.full(n, np.nan)
    for i in range(k, n - k):
        w_hi = hi[i - k:i + k + 1]
        w_lo = lo[i - k:i + k + 1]
        if hi[i] == w_hi.max():
            ph[i] = hi[i]
        if lo[i] == w_lo.min():
            pl[i] = lo[i]
    return ph, pl


def eterna_dengan_filter(h: pd.DataFrame, mode: str) -> pd.DataFrame:
    """Port setia eterna (blocking_akurat.eterna_trades) + hook penyaring S/R.

    mode: 'dasar' | 'A_ruang' | 'B_dinding' | 'C_breakout'
    """
    se, st = supertrend(h, P, ME), supertrend(h, P, MT)
    sd = se.where(se != se.shift(1)).shift(1).to_numpy()
    td = st.shift(1).to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(P).min().shift(1).to_numpy()
    shi = h["high"].rolling(P).max().shift(1).to_numpy()
    ph, pl = pivots(h, PIVOT_K)

    pos = 0; entry = sl = tp = 0.0; ei = 0
    out = []
    for i in range(1, len(h)):
        if pos != 0:
            hit = (sl if lo[i] <= sl else (tp if hi[i] >= tp else None)) if pos == 1 \
                  else (sl if hi[i] >= sl else (tp if lo[i] <= tp else None))
            if hit is not None:
                out.append((h.index[ei], h.index[i], pos, entry, hit)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((h.index[ei], h.index[i], pos, entry, o[i])); pos = 0
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s:
            continue
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(o[i] - raw)
        if dist < MIN_SL:
            continue

        # ---------------- penyaring S/R ----------------
        if mode != "dasar":
            px = o[i]
            tp_dist = TPR * dist
            # HANYA pivot yang sudah terkonfirmasi sebelum bar i: indeks <= i-K-1
            batas = i - PIVOT_K - 1
            awal = max(0, batas - PIVOT_LOOKBACK)
            if batas <= awal:
                continue
            lv_hi = ph[awal:batas + 1]; lv_hi = lv_hi[~np.isnan(lv_hi)]
            lv_lo = pl[awal:batas + 1]; lv_lo = lv_lo[~np.isnan(lv_lo)]

            if s == 1:
                atas = lv_hi[lv_hi > px]
                dekat = atas.min() - px if len(atas) else np.inf
            else:
                bawah = lv_lo[lv_lo < px]
                dekat = px - bawah.max() if len(bawah) else np.inf

            if mode == "A_ruang":
                # jalan menuju TP tidak boleh terhalang level
                if dekat < tp_dist:
                    continue
            elif mode == "B_dinding":
                # jangan masuk tepat di depan dinding (dalam 0.25x jarak stop)
                if dekat < 0.25 * dist:
                    continue
            elif mode == "C_breakout":
                # harga harus SUDAH menembus level swing terdekat searah sinyal
                if s == 1:
                    bawah = lv_hi[lv_hi < px]
                    if len(bawah) == 0:
                        continue
                else:
                    atas = lv_lo[lv_lo > px]
                    if len(atas) == 0:
                        continue

        pos, entry, ei = s, o[i], i
        sl = o[i] - dist if s == 1 else o[i] + dist
        tp = o[i] + TPR * dist if s == 1 else o[i] - TPR * dist

    t = pd.DataFrame(out, columns=["masuk", "keluar", "arah", "px_in", "px_out"])
    t["pnl"] = (t.px_out - t.px_in) * t.arah * LOT * 100 - COST
    return t


def ukur(t: pd.DataFrame, a: str, b: str) -> dict:
    m = t[(t.masuk >= pd.Timestamp(a, tz="UTC")) & (t.masuk < pd.Timestamp(b, tz="UTC"))]
    if len(m) == 0:
        return {"n": 0, "net": 0.0, "hijau": 0.0, "pf": 0.0, "dd": 0.0}
    d = m.set_index("masuk").pnl
    bln = d.resample("ME").sum()
    eq = CAPITAL + d.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    w, l = d[d > 0].sum(), -d[d < 0].sum()
    return {"n": len(d), "net": float(d.sum()), "hijau": 100 * float((bln > 0).mean()),
            "pf": float(w / l) if l else np.inf, "dd": 100 * dd, "bln": bln}


def main():
    print("Membangun ...", flush=True)
    h = load_h1()

    dasar = eterna_dengan_filter(h, "dasar")
    print(f"\n  VALIDASI PORT: {len(dasar)} trade, net ${dasar.pnl.sum():.2f}")
    print(f"  acuan blocking_akurat.py : 584 trade, net $2789.95")
    ok = abs(len(dasar) - 584) <= 2 and abs(dasar.pnl.sum() - 2789.95) < 5
    print(f"  >> {'COCOK - port bisa dipakai' if ok else 'TIDAK COCOK - JANGAN percaya hasil di bawah'}")
    if not ok:
        return

    d5 = ukur(dasar, "2021-01-01", "2026-01-01")
    print("\n" + "=" * 100)
    print("HASIL - fokus 2021-2025 (di situ masalah eterna, bukan di 2026)")
    print("=" * 100)
    print(f"  {'varian':<14}{'n':>5}{'net$':>10}{'vs dasar':>10}{'PF':>7}{'hijau%':>8}{'maxDD%':>9}   status")
    print(f"  {'dasar':<14}{d5['n']:>5}{d5['net']:>10.2f}{'-':>10}{d5['pf']:>7.2f}{d5['hijau']:>8.0f}{d5['dd']:>9.1f}   acuan")

    hasil = {}
    for mode in ("A_ruang", "B_dinding", "C_breakout"):
        t = eterna_dengan_filter(h, mode)
        m = ukur(t, "2021-01-01", "2026-01-01")
        hasil[mode] = (t, m)
        naik = 100 * (m["net"] - d5["net"]) / abs(d5["net"]) if d5["net"] else 0
        sisa = 100 * m["n"] / d5["n"] if d5["n"] else 0
        lulus = (naik >= 25) and (sisa >= 60) and (m["hijau"] >= d5["hijau"])
        gagal = []
        if naik < 25: gagal.append(f"net {naik:+.0f}%")
        if sisa < 60: gagal.append(f"trade tinggal {sisa:.0f}%")
        if m["hijau"] < d5["hijau"]: gagal.append(f"hijau turun {m['hijau']:.0f}%")
        st = "LULUS" if lulus else "gagal: " + ", ".join(gagal)
        print(f"  {mode:<14}{m['n']:>5}{m['net']:>10.2f}{naik:>9.0f}%{m['pf']:>7.2f}"
              f"{m['hijau']:>8.0f}{m['dd']:>9.1f}   {st}")

    print("\n" + "=" * 100)
    print("2026 TERPISAH - untuk melihat apakah filter cuma memperbaiki tahun anomali")
    print("=" * 100)
    d26 = ukur(dasar, "2026-01-01", "2027-01-01")
    print(f"  {'dasar':<14}{d26['n']:>5}{d26['net']:>10.2f}")
    for mode, (t, _) in hasil.items():
        m26 = ukur(t, "2026-01-01", "2027-01-01")
        print(f"  {mode:<14}{m26['n']:>5}{m26['net']:>10.2f}")

    print("\n" + "=" * 100)
    print("DEFLATED SHARPE - N naik karena tes ini sendiri menambah percobaan")
    print("=" * 100)
    bln_dasar = dasar.set_index("masuk").pnl.resample("ME").sum()
    sr, p = dsr(bln_dasar / CAPITAL, 1900)
    print(f"  dasar          N=1900  Sharpe {sr:.3f}  DSR {p:.4f}")
    for mode, (t, _) in hasil.items():
        b = t.set_index("masuk").pnl.resample("ME").sum()
        sr, p = dsr(b / CAPITAL, 1903)
        print(f"  {mode:<14} N=1903  Sharpe {sr:.3f}  DSR {p:.4f}")
    print("\n  N=1903 karena tiga hipotesis ini menambah tiga percobaan. Kalau kita menyapu")
    print("  puluhan varian, N melonjak dan ambangnya ikut naik - itu sebabnya sapuan")
    print("  dihindari, bukan karena malas.")

    print("\n" + "=" * 100)


if __name__ == "__main__":
    main()
