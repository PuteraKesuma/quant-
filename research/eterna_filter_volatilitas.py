"""ETERNA + filter rezim volatilitas — BUKAN sleeve baru, cuma saringan pada yang ada.

KENAPA ARAH INI YANG PALING MASUK AKAL DARI YANG TERSISA:
Semua kandidat sleeve ketiga hari ini gugur. Tapi filter rezim berbeda sifatnya - dia
tidak menambah proses, tidak menambah mode kegagalan, tidak menambah kode yang bisa
mati diam-diam. Kalau berhasil, dia memangkas kerugian eterna di rezim yang salah.

HIPOTESISNYA MEKANIS, bukan tebakan:
Eterna trend-follower (dual Supertrend). Trend-follower rugi saat pasar menyamping dan
untung saat bergerak berarah. Kalau rezim menyamping bisa dikenali SEBELUM entry,
tahun-tahun rugi eterna seharusnya membaik.

Bukti bahwa masalahnya memang rezim: 2021 +89, 2022 +44, 2023 +122, 2024 +222,
2025 +249, 2026 +2063. Emas baru benar-benar tren di 2025-2026.

TIGA UKURAN, ditetapkan di depan, AMBANG TIDAK DISETEL:
  V1 ATR relatif    ATR(14) H1 > median ATR 100 bar terakhir
  V2 vol realisasi  stdev return harian 20 hari > median 100 hari
  V3 efficiency     Kaufman ER 20 bar > median 100 bar
Ambang semuanya MEDIAN dirinya sendiri - tanpa parameter yang bisa dicocok-cocokkan.
Efficiency Ratio adalah ukuran baku tren-vs-menyamping: |perubahan bersih| dibagi
|total jarak tempuh|. Tinggi = berarah, rendah = bolak-balik.

ANTI-LOOKAHEAD: semua ukuran dihitung dari bar TERTUTUP dan di-shift(1) sebelum
dipakai memutuskan. Median rolling juga ikut ter-shift.

KRITERIA LULUS, ditulis sebelum dijalankan:
  1. net 2021-2025 naik >= 25%          (di situ masalahnya, bukan di 2026)
  2. trade tersisa >= 60%               (kalau anjlok, dia memetik bukan menyaring)
  3. porsi bulan hijau tidak turun
  4. Calmar PORTOFOLIO membaik          (pelajaran hari ini: ambang saja tidak cukup)
Gagal salah satu = ditolak, tanpa penyetelan penyelamatan.

Jalankan: python research/eterna_filter_volatilitas.py
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
from blocking_akurat import load_h1, supertrend
from portfolio_audit import nas_dollars

LOT, COST, MIN_SL = 0.01, 0.50, 0.30
P, ME, MT, TPR = 16, 1.8, 3.8, 4.0
CAPITAL = 548.19


def atr_h1(h, n=14):
    pc = h["close"].shift(1)
    tr = pd.concat([h["high"] - h["low"], (h["high"] - pc).abs(),
                    (h["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def ukuran_rezim(h: pd.DataFrame) -> dict:
    """Kembalikan mask boolean per bar: True = rezim BOLEH trade.

    Semua di-shift(1): keputusan di bar i hanya boleh memakai informasi sampai i-1.
    """
    out = {}

    # V1 ATR relatif terhadap median 100 bar
    a = atr_h1(h)
    out["V1_atr"] = (a > a.rolling(100).median()).shift(1).fillna(False).to_numpy()

    # V2 vol realisasi harian: stdev return 20 hari vs median 100 hari
    d = h["close"].resample("1D").last().dropna()
    r = d.pct_change()
    v20 = r.rolling(20).std()
    med = v20.rolling(100).median()
    ok_d = (v20 > med).shift(1)                       # shift di level HARIAN
    peta = {ts.date(): bool(v) for ts, v in ok_d.items() if v == v}
    out["V2_volharian"] = np.array([peta.get(t.date(), False) for t in h.index])

    # V3 Kaufman Efficiency Ratio 20 bar vs median 100
    c = h["close"]
    perubahan = (c - c.shift(20)).abs()
    jarak = c.diff().abs().rolling(20).sum()
    er = perubahan / jarak.replace(0, np.nan)
    out["V3_efficiency"] = (er > er.rolling(100).median()).shift(1).fillna(False).to_numpy()

    return out


def eterna(h: pd.DataFrame, mask: np.ndarray | None = None) -> pd.DataFrame:
    """Port eterna yang SUDAH DIVALIDASI (584 trade, $2789.95) + hook filter rezim."""
    se, st = supertrend(h, P, ME), supertrend(h, P, MT)
    sd = se.where(se != se.shift(1)).shift(1).to_numpy()
    td = st.shift(1).to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(P).min().shift(1).to_numpy()
    shi = h["high"].rolling(P).max().shift(1).to_numpy()

    pos = 0; entry = sl = tp = 0.0; ei = 0
    out = []
    for i in range(1, len(h)):
        if pos != 0:
            hit = (sl if lo[i] <= sl else (tp if hi[i] >= tp else None)) if pos == 1 \
                  else (sl if hi[i] >= sl else (tp if lo[i] <= tp else None))
            if hit is not None:
                out.append((h.index[ei], pos, entry, hit)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((h.index[ei], pos, entry, o[i])); pos = 0
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s:
            continue
        if mask is not None and not mask[i]:
            continue                                    # rezim tidak mendukung -> lewati
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(o[i] - raw)
        if dist < MIN_SL:
            continue
        pos, entry, ei = s, o[i], i
        sl = o[i] - dist if s == 1 else o[i] + dist
        tp = o[i] + TPR * dist if s == 1 else o[i] - TPR * dist

    t = pd.DataFrame(out, columns=["masuk", "arah", "px_in", "px_out"])
    t["pnl"] = (t.px_out - t.px_in) * t.arah * LOT * 100 - COST
    return t


def ukur(t, a, b):
    m = t[(t.masuk >= pd.Timestamp(a, tz="UTC")) & (t.masuk < pd.Timestamp(b, tz="UTC"))]
    if len(m) == 0:
        return {"n": 0, "net": 0.0, "hijau": 0.0, "pf": 0.0}
    d = m.set_index("masuk").pnl
    bln = d.resample("ME").sum()
    w, l = d[d > 0].sum(), -d[d < 0].sum()
    return {"n": len(d), "net": float(d.sum()), "hijau": 100 * float((bln > 0).mean()),
            "pf": float(w / l) if l > 0 else 99}


def port(m, modal=CAPITAL):
    eq = modal + m.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yrs = len(m) / 12.0
    cagr = (eq.iloc[-1] / modal) ** (1 / yrs) - 1
    return {"CAGR%": 100 * cagr, "maxDD%": 100 * dd,
            "Calmar": cagr / abs(dd) if dd else np.nan}


def main():
    print("Membangun ...", flush=True)
    h = load_h1()
    dasar = eterna(h)
    print(f"\n  VALIDASI PORT: {len(dasar)} trade, net ${dasar.pnl.sum():.2f}")
    print(f"  acuan          : 584 trade, $2789.95")
    if abs(len(dasar) - 584) > 2 or abs(dasar.pnl.sum() - 2789.95) > 5:
        print("  >> TIDAK COCOK - berhenti.")
        return
    print("  >> COCOK")

    mask = ukuran_rezim(h)
    for k, v in mask.items():
        print(f"    {k:<16} rezim mendukung {100*v.mean():.0f}% dari waktu")

    d5 = ukur(dasar, "2021-01-01", "2026-01-01")
    d26 = ukur(dasar, "2026-01-01", "2027-01-01")

    orb = nas_dollars()
    if orb.index.tz is None:
        orb.index = orb.index.tz_localize("UTC")
    mon_orb = orb.resample("ME").sum()

    def calmar_port(t):
        j = pd.DataFrame({"ORB": mon_orb,
                          "ET": t.set_index("masuk").pnl.resample("ME").sum()}).fillna(0.0)
        j = j.loc[(j != 0).any(axis=1)]
        return port(j["ORB"] + j["ET"])

    c0 = calmar_port(dasar)

    print("\n" + "=" * 104)
    print("HASIL — fokus 2021-2025, di situ masalah eterna")
    print("=" * 104)
    print(f"  {'varian':<18}{'n':>6}{'net$':>10}{'vs dasar':>10}{'PF':>7}{'hijau%':>8}"
          f"{'2026$':>10}{'Calmar port':>13}   status")
    print(f"  {'dasar':<18}{d5['n']:>6}{d5['net']:>10.2f}{'-':>10}{d5['pf']:>7.2f}"
          f"{d5['hijau']:>8.0f}{d26['net']:>10.2f}{c0['Calmar']:>13.2f}   acuan")

    for nama, mk in mask.items():
        t = eterna(h, mk)
        m5 = ukur(t, "2021-01-01", "2026-01-01")
        m26 = ukur(t, "2026-01-01", "2027-01-01")
        cp = calmar_port(t)
        naik = 100 * (m5["net"] - d5["net"]) / abs(d5["net"]) if d5["net"] else 0
        sisa = 100 * m5["n"] / d5["n"] if d5["n"] else 0
        gag = []
        if naik < 25: gag.append(f"net {naik:+.0f}%")
        if sisa < 60: gag.append(f"trade {sisa:.0f}%")
        if m5["hijau"] < d5["hijau"]: gag.append(f"hijau {m5['hijau']:.0f}%")
        if cp["Calmar"] <= c0["Calmar"]: gag.append(f"Calmar {cp['Calmar']:.2f}")
        st = "LULUS" if not gag else "gagal: " + ", ".join(gag)
        print(f"  {nama:<18}{m5['n']:>6}{m5['net']:>10.2f}{naik:>9.0f}%{m5['pf']:>7.2f}"
              f"{m5['hijau']:>8.0f}{m26['net']:>10.2f}{cp['Calmar']:>13.2f}   {st}")

    print("\n" + "=" * 104)
    print("PER TAHUN — apakah filter memperbaiki tahun-tahun yang lemah?")
    print("=" * 104)
    tabel = {"dasar": dasar}
    for nama, mk in mask.items():
        tabel[nama] = eterna(h, mk)
    print(f"  {'tahun':<8}" + "".join(f"{k:>16}" for k in tabel))
    for y in range(2021, 2027):
        baris = f"  {y:<8}"
        for k, t in tabel.items():
            m = ukur(t, f"{y}-01-01", f"{y+1}-01-01")
            baris += f"{m['net']:>16.2f}"
        print(baris)

    print("\n" + "=" * 104)


if __name__ == "__main__":
    main()
