"""Menaikkan frekuensi SMC TANPA membuat rugi — permintaan user 2026-08-13.

PERSOALANNYA: H4-B yang dipasang cuma ~17-20 order/tahun (~0.05/hari). User ingin
lebih sering, tapi tidak mau menukar itu dengan kerugian.

Turun timeframe begitu saja SUDAH DIUJI dan jawabannya tidak (research/smc_xau_backtest.py):
    M15  A -953  B -397  C -198  D  +55
    H1   A   -5  B -128  C +132  D  -85
    H4   A -169  B +631  C -161  D  +42
Konfigurasi yang menang di H4 (B = OB+BOS+FVG) justru RUGI di H1 dan M15.

IDE YANG DIUJI DI SINI — MULTI-TIMEFRAME, dan ini cara SMC sebenarnya dipakai:
    H4 menentukan BIAS (arah BOS terakhir), H1 memberi ENTRY (zona OB searah bias).
Frekuensi naik karena entry dicari di H1, tapi disiplinnya tetap dari H4. Ini IDE
STRUKTURAL BARU, bukan penyetelan ulang parameter yang sudah ada.

ANTI-LOOKAHEAD YANG KRITIS DI SINI: bias H4 dari bar yang berlabel awal (mis. 08:00)
baru DIKETAHUI saat bar itu TUTUP (12:00). Jadi bias dipetakan ke bar H1 pertama yang
mulai PADA ATAU SESUDAH akhir bar H4 itu. Salah di titik ini = lookahead 4 jam dan
seluruh hasilnya palsu (persis cacat yang membatalkan riset Golden).

Batas 2 setup/hari yang baru dipasang juga disimulasikan, supaya angka yang dilaporkan
adalah angka yang BENAR-BENAR akan terjadi live, bukan versi tanpa batas.

Jalankan: python research/smc_frekuensi.py
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
from smc_xau_backtest import (load_m1, tf, jalankan, swing_pivots, malam,
                              LOT, SPREAD, SWAP_LONG, SWAP_SHORT, CAPITAL)

MAX_PER_DAY = 2


def bias_h4(h4: pd.DataFrame, k: int = 3) -> pd.Series:
    """Arah BOS H4 terakhir, DIBERI CAP WAKTU saat bar H4 itu TUTUP (bukan saat mulai)."""
    hi, lo, c = h4["high"].to_numpy(), h4["low"].to_numpy(), h4["close"].to_numpy()
    sh, sl = swing_pivots(h4, k)
    i_sh = i_sl = 0; last_sh = last_sl = None
    sh_break = sl_break = False
    arah = 0
    out_t, out_v = [], []
    for j in range(1, len(h4)):
        while i_sh < len(sh) and sh[i_sh][2] <= j:
            last_sh = sh[i_sh][1]; i_sh += 1; sh_break = False
        while i_sl < len(sl) and sl[i_sl][2] <= j:
            last_sl = sl[i_sl][1]; i_sl += 1; sl_break = False
        if last_sh is not None and not sh_break and c[j] > last_sh:
            arah = 1; sh_break = True
        elif last_sl is not None and not sl_break and c[j] < last_sl:
            arah = -1; sl_break = True
        # bar H4 berlabel awal -> baru diketahui 4 jam kemudian
        out_t.append(h4.index[j] + pd.Timedelta("4h")); out_v.append(arah)
    return pd.Series(out_v, index=pd.DatetimeIndex(out_t))


def jalankan_mtf(h1: pd.DataFrame, bias: pd.Series, *, k=3, ob_lookback=10,
                 expiry=12, rr=2.0, buffer_frac=0.10, cap_harian=None) -> pd.DataFrame:
    """Entry H1 (OB+BOS+FVG) TAPI hanya searah bias H4."""
    b = bias.reindex(h1.index, method="ffill")     # sudah bercap waktu tutup H4
    bv = b.to_numpy()
    o = h1["open"].to_numpy(); c = h1["close"].to_numpy()
    hi = h1["high"].to_numpy(); lo = h1["low"].to_numpy()
    idx = h1.index; n = len(h1)
    sh, sl_piv = swing_pivots(h1, k)

    i_sh = i_sl = 0; last_sh = last_sl = None
    sh_break = sl_break = False
    pos = 0; entry = p_sl = p_tp = 0.0; ei = 0
    pend = None
    hari, dipakai = None, 0
    out = []

    for j in range(1, n):
        d = idx[j].strftime("%Y-%m-%d")
        if d != hari:
            hari, dipakai = d, 0
        while i_sh < len(sh) and sh[i_sh][2] <= j:
            last_sh = sh[i_sh][1]; i_sh += 1; sh_break = False
        while i_sl < len(sl_piv) and sl_piv[i_sl][2] <= j:
            last_sl = sl_piv[i_sl][1]; i_sl += 1; sl_break = False

        if pos != 0:
            if pos == 1:
                if lo[j] <= p_sl:
                    out.append((idx[ei], idx[j], 1, entry, p_sl, "SL")); pos = 0
                elif hi[j] >= p_tp:
                    out.append((idx[ei], idx[j], 1, entry, p_tp, "TP")); pos = 0
            else:
                if hi[j] >= p_sl:
                    out.append((idx[ei], idx[j], -1, entry, p_sl, "SL")); pos = 0
                elif lo[j] <= p_tp:
                    out.append((idx[ei], idx[j], -1, entry, p_tp, "TP")); pos = 0

        if pend is not None and pos == 0:
            a_, px_, s_, t_, exp_, _ = pend
            kena = (lo[j] <= px_) if a_ == 1 else (hi[j] >= px_)
            if kena:
                pos, entry, ei, p_sl, p_tp = a_, px_, j, s_, t_; pend = None
            elif j >= exp_:
                pend = None
        elif pend is not None:
            pend = None

        arah = 0
        if last_sh is not None and not sh_break and c[j] > last_sh:
            arah = 1; sh_break = True
        elif last_sl is not None and not sl_break and c[j] < last_sl:
            arah = -1; sl_break = True
        if arah == 0 or pos != 0 or pend is not None:
            continue
        if bv[j] != arah:                       # GATE: harus searah bias H4
            continue
        if cap_harian is not None and dipakai >= cap_harian:
            continue

        # Order Block
        awal = max(0, j - ob_lookback); i_ob = None
        for i in range(j - 1, awal - 1, -1):
            bear = c[i] < o[i]
            if (arah == 1 and bear) or (arah == -1 and not bear):
                i_ob = i; break
        if i_ob is None:
            continue
        ob_lo, ob_hi = lo[i_ob], hi[i_ob]
        if ob_hi <= ob_lo:
            continue
        # FVG di dalam leg impulse (wajib, seperti H4-B)
        fvg = False
        for t in range(max(i_ob + 1, 1), min(j, n - 1)):
            if arah == 1 and lo[t + 1] > hi[t - 1]:
                fvg = True; break
            if arah == -1 and hi[t + 1] < lo[t - 1]:
                fvg = True; break
        if not fvg:
            continue

        buf = (ob_hi - ob_lo) * buffer_frac
        if arah == 1:
            px = ob_hi; s = ob_lo - buf
            if px <= s or px >= c[j]:
                continue
            t_ = px + rr * (px - s)
        else:
            px = ob_lo; s = ob_hi + buf
            if px >= s or px <= c[j]:
                continue
            t_ = px - rr * (s - px)
        pend = (arah, px, s, t_, j + expiry, j)
        dipakai += 1

    t = pd.DataFrame(out, columns=["masuk", "keluar", "arah", "px_in", "px_out", "sebab"])
    if len(t) == 0:
        return t
    t["kotor"] = (t.px_out - t.px_in) * t.arah * LOT * 100
    t["malam"] = [malam(a, b_) for a, b_ in zip(t.masuk, t.keluar)]
    t["swap"] = np.where(t.arah == 1, t.malam * SWAP_LONG, t.malam * SWAP_SHORT)
    t["pnl"] = t.kotor - SPREAD + t.swap
    return t


def ringkas(t: pd.DataFrame, label: str, hari_total: float) -> dict:
    if len(t) < 15:
        return {"varian": label, "n": len(t), "per hari": 0.0, "net$": 0.0,
                "PF": 0.0, "maxDD%": 0.0, "thn+": "-", "net/DD": 0.0}
    d = t.set_index("masuk").pnl
    eq = CAPITAL + d.cumsum()
    dd = abs(float(((eq - eq.cummax()) / eq.cummax()).min()))
    w, l = d[d > 0].sum(), -d[d < 0].sum()
    thn = d.groupby(d.index.year).sum()
    return {"varian": label, "n": len(d), "per hari": round(len(d) / hari_total, 2),
            "net$": round(d.sum(), 2), "PF": round(w / l if l else 99, 2),
            "maxDD%": round(-100 * dd, 1),
            "thn+": f"{int((thn > 0).sum())}/{len(thn)}",
            "net/DD": round(d.sum() / (100 * dd), 1) if dd > 0 else 0.0}


def main():
    print("Membangun ...", flush=True)
    m1 = load_m1()
    h4 = tf(m1, "4h"); h1 = tf(m1, "1h")
    hari_total = (m1.index[-1] - m1.index[0]).days
    print(f"  periode {hari_total} hari kalender ({hari_total/365.25:.1f} tahun)")

    acuan = jalankan(h4, k=3, ob_lookback=10, expiry=12, rr=2.0,
                     buffer_frac=0.10, pakai_fvg=True, pakai_sweep=False)
    bias = bias_h4(h4)

    print("\n" + "=" * 104)
    print("MULTI-TIMEFRAME: bias H4 + entry H1 (ide struktural BARU — 3 trial)")
    print("=" * 104)
    rows = [ringkas(acuan, "H4-B (yang terpasang)", hari_total)]
    for e in (12, 24, 48):
        t = jalankan_mtf(h1, bias, expiry=e, cap_harian=MAX_PER_DAY)
        rows.append(ringkas(t, f"MTF H4bias+H1entry exp{e}", hari_total))
    print(pd.DataFrame(rows).to_string(index=False))

    print("\n" + "=" * 104)
    print("PEMBANDING: turun timeframe polos (sudah diuji sebelumnya, dibawa ke sini)")
    print("=" * 104)
    rows2 = []
    for rule, lab, kw in (("1h", "H1 C (+sweep)", dict(pakai_fvg=False, pakai_sweep=True)),
                          ("15min", "M15 D (semua)", dict(pakai_fvg=True, pakai_sweep=True))):
        t = jalankan(tf(m1, rule), k=3, ob_lookback=10, expiry=12, rr=2.0,
                     buffer_frac=0.10, **kw)
        rows2.append(ringkas(t, lab, hari_total))
    print(pd.DataFrame(rows2).to_string(index=False))

    print("\n" + "=" * 104)
    print("EFEK BATAS 2 SETUP/HARI pada varian MTF terbaik menurut net/DD")
    print("=" * 104)
    best_e = None; best_v = -1e9
    for e in (12, 24, 48):
        t = jalankan_mtf(h1, bias, expiry=e, cap_harian=MAX_PER_DAY)
        r = ringkas(t, "x", hari_total)
        if r["n"] >= 15 and r["net/DD"] > best_v:
            best_v, best_e = r["net/DD"], e
    if best_e is None:
        print("  tidak ada varian MTF dengan sampel memadai.")
    else:
        a = ringkas(jalankan_mtf(h1, bias, expiry=best_e, cap_harian=None),
                    f"exp{best_e} TANPA batas", hari_total)
        b = ringkas(jalankan_mtf(h1, bias, expiry=best_e, cap_harian=MAX_PER_DAY),
                    f"exp{best_e} DENGAN batas 2/hari", hari_total)
        print(pd.DataFrame([a, b]).to_string(index=False))

    print("\n" + "=" * 104)


if __name__ == "__main__":
    main()
