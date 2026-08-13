"""KONFIRMASI ENTRY DI M5 — permintaan user 2026-08-13.

SEKARANG: limit pasif di ujung zona OB; broker mengisi otomatis begitu harga menyentuh.
DIMINTA : harga menyentuh zona -> tunggu KONFIRMASI di M5 -> baru masuk.

Itu bukan penyetelan kecil, itu mekanisme eksekusi yang berbeda:
  * harga masuk jadi LEBIH BURUK (masuk setelah konfirmasi, bukan di ujung zona)
  * sebagian zona tidak pernah terkonfirmasi -> jumlah trade TURUN
  * imbalannya: zona yang ditembus lurus tanpa reaksi jadi tersaring

Aturan konfirmasi yang diuji (mekanis, bisa diaudit):
  setelah harga menyentuh zona, dalam `konfirm_bars` bar M5 harus ada BOS M5
  SEARAH trade (close menembus swing M5 terkonfirmasi terakhir). Kalau ada ->
  masuk di close bar M5 itu. Kalau tidak -> tidak ada trade.

SL dan TP tetap di level ABSOLUT yang sama dengan mesin (dari zona OB), supaya yang
dibandingkan murni efek konfirmasinya, bukan efek SL/TP yang ikut berubah.

Jalankan: python research/smc_konfirmasi_m5.py
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
from smc_xau_backtest import (load_m1, tf, swing_pivots, malam,
                              LOT, SPREAD, SWAP_LONG, SWAP_SHORT, CAPITAL)


def zona_terarm(h, use_fvg, use_sweep, k=3, ob=10, expiry=12, buf=0.10, rr=2.0, sw=5):
    """Kembalikan daftar zona ter-arm: (t_bos, arah, px, sl, tp, t_kedaluwarsa)."""
    o = h["open"].to_numpy(); c = h["close"].to_numpy()
    hi = h["high"].to_numpy(); lo = h["low"].to_numpy(); n = len(h)
    idx = h.index
    sh, sl_p = swing_pivots(h, k)

    def lvl(piv):
        out = np.full(n, np.nan); p = 0; cur = np.nan
        for j in range(n):
            while p < len(piv) and piv[p][2] <= j:
                cur = piv[p][1]; p += 1
            out[j] = cur
        return out
    Lsl, Lsh = lvl(sl_p), lvl(sh)

    i_sh = i_sl = 0; lsh = lsl = None; bsh = bsl = False
    pos = 0; psl = ptp = 0.0; pend = None
    out = []
    for j in range(1, n):
        while i_sh < len(sh) and sh[i_sh][2] <= j:
            lsh = sh[i_sh][1]; i_sh += 1; bsh = False
        while i_sl < len(sl_p) and sl_p[i_sl][2] <= j:
            lsl = sl_p[i_sl][1]; i_sl += 1; bsl = False
        if pos != 0:
            if pos == 1 and (lo[j] <= psl or hi[j] >= ptp): pos = 0
            elif pos == -1 and (hi[j] >= psl or lo[j] <= ptp): pos = 0
        if pend is not None and pos == 0:
            a, px, s, t, e = pend
            if (lo[j] <= px) if a == 1 else (hi[j] >= px):
                pos, psl, ptp = a, s, t; pend = None
            elif j >= e:
                pend = None
        elif pend is not None:
            pend = None
        arah = 0
        if lsh is not None and not bsh and c[j] > lsh: arah = 1; bsh = True
        elif lsl is not None and not bsl and c[j] < lsl: arah = -1; bsl = True
        if arah == 0 or pos != 0 or pend is not None:
            continue
        iob = None
        for i in range(j - 1, max(0, j - ob) - 1, -1):
            bear = c[i] < o[i]
            if (arah == 1 and bear) or (arah == -1 and not bear):
                iob = i; break
        if iob is None: continue
        oblo, obhi = lo[iob], hi[iob]
        if obhi <= oblo: continue
        if use_fvg:
            f = False
            for t_ in range(max(iob + 1, 1), min(j, n - 1)):
                if arah == 1 and lo[t_ + 1] > hi[t_ - 1]: f = True; break
                if arah == -1 and hi[t_ + 1] < lo[t_ - 1]: f = True; break
            if not f: continue
        if use_sweep:
            L = Lsl if arah == 1 else Lsh; f = False
            for t_ in range(max(0, iob - sw), iob + 1):
                r = L[t_]
                if r != r: continue
                if arah == 1 and lo[t_] < r and c[t_] > r: f = True; break
                if arah == -1 and hi[t_] > r and c[t_] < r: f = True; break
            if not f: continue
        b = (obhi - oblo) * buf
        if arah == 1:
            px = obhi; s = oblo - b
            if px <= s or px >= c[j]: continue
            t = px + rr * (px - s)
        else:
            px = oblo; s = obhi + b
            if px >= s or px <= c[j]: continue
            t = px - rr * (s - px)
        pend = (arah, px, s, t, j + expiry)
        out.append((idx[j], arah, px, s, t, idx[min(j + expiry, n - 1)]))
    return out


def simulasi(zona, m5, konfirm_bars=None, k5=3):
    """konfirm_bars=None -> limit pasif (acuan). Angka -> wajib BOS M5 dulu."""
    hi5 = m5["high"].to_numpy(); lo5 = m5["low"].to_numpy()
    c5 = m5["close"].to_numpy(); idx5 = m5.index
    sh5, sl5 = (swing_pivots(m5, k5) if konfirm_bars else ([], []))
    n5 = len(m5)
    if konfirm_bars:
        def lvl(piv):
            out = np.full(n5, np.nan); p = 0; cur = np.nan
            for j in range(n5):
                while p < len(piv) and piv[p][2] <= j:
                    cur = piv[p][1]; p += 1
                out[j] = cur
            return out
        L5sh, L5sl = lvl(sh5), lvl(sl5)

    trades = []
    sibuk_sampai = None
    for (t_bos, arah, px, s, t, t_exp) in zona:
        if sibuk_sampai is not None and t_bos < sibuk_sampai:
            continue
        seg = m5.loc[t_bos:t_exp]
        if len(seg) < 2:
            continue
        a = idx5.searchsorted(seg.index[0]); b = idx5.searchsorted(seg.index[-1])
        sentuh = None
        for j in range(a + 1, b + 1):
            if (lo5[j] <= px) if arah == 1 else (hi5[j] >= px):
                sentuh = j; break
        if sentuh is None:
            continue

        if konfirm_bars is None:
            j_in, px_in = sentuh, px
        else:
            j_in = None
            for j in range(sentuh, min(sentuh + konfirm_bars, n5)):
                ref = L5sh[j] if arah == 1 else L5sl[j]
                if ref != ref:
                    continue
                if (arah == 1 and c5[j] > ref) or (arah == -1 and c5[j] < ref):
                    j_in = j; break
            if j_in is None:
                continue
            px_in = c5[j_in]
            if (arah == 1 and px_in >= t) or (arah == -1 and px_in <= t):
                continue                      # konfirmasi terlambat: sudah lewat TP
            if (arah == 1 and px_in <= s) or (arah == -1 and px_in >= s):
                continue                      # sudah lewat SL

        keluar = None
        for j in range(j_in + 1, n5):
            if arah == 1:
                if lo5[j] <= s: keluar = (idx5[j], s, "SL"); break
                if hi5[j] >= t: keluar = (idx5[j], t, "TP"); break
            else:
                if hi5[j] >= s: keluar = (idx5[j], s, "SL"); break
                if lo5[j] <= t: keluar = (idx5[j], t, "TP"); break
        if keluar is None:
            continue
        trades.append((idx5[j_in], keluar[0], arah, px_in, keluar[1], keluar[2]))
        sibuk_sampai = keluar[0]

    d = pd.DataFrame(trades, columns=["masuk", "keluar", "arah", "px_in", "px_out", "sebab"])
    if len(d) == 0:
        return d
    d["kotor"] = (d.px_out - d.px_in) * d.arah * LOT * 100
    d["malam"] = [malam(x, y) for x, y in zip(d.masuk, d.keluar)]
    d["swap"] = np.where(d.arah == 1, d.malam * SWAP_LONG, d.malam * SWAP_SHORT)
    d["pnl"] = d.kotor - SPREAD + d.swap
    return d


def ringkas(d, label, n_zona):
    if len(d) < 10:
        return {"varian": label, "trade": len(d), "isi%": 0, "net$": 0.0,
                "PF": 0.0, "WR%": 0, "maxDD%": 0.0, "net/DD": 0.0}
    p = d.set_index("masuk").pnl
    eq = CAPITAL + p.cumsum()
    dd = abs(float(((eq - eq.cummax()) / eq.cummax()).min()))
    w, l = p[p > 0].sum(), -p[p < 0].sum()
    return {"varian": label, "trade": len(p), "isi%": round(100 * len(p) / n_zona),
            "net$": round(p.sum(), 2), "PF": round(w / l if l else 99, 2),
            "WR%": round(100 * (p > 0).mean()), "maxDD%": round(-100 * dd, 1),
            "net/DD": round(p.sum() / (100 * dd), 1) if dd else 0.0}


def main():
    print("Membangun ...", flush=True)
    m1 = load_m1()
    m5 = tf(m1, "5min")
    print(f"  M5: {len(m5):,} bar")

    for nama, rule, fvg, swp in (("H4-B (920643)", "4h", True, False),
                                 ("H1-C (920644)", "1h", False, True)):
        h = tf(m1, rule)
        zona = zona_terarm(h, fvg, swp)
        print("\n" + "=" * 96)
        print(f"{nama} — {len(zona)} zona ter-arm")
        print("=" * 96)
        rows = [ringkas(simulasi(zona, m5, None), "limit pasif (SEKARANG)", len(zona))]
        for kb in (3, 6, 12, 24):
            rows.append(ringkas(simulasi(zona, m5, kb),
                                f"konfirmasi M5 dalam {kb} bar ({kb*5}m)", len(zona)))
        print(pd.DataFrame(rows).to_string(index=False))

    print("\n" + "=" * 96)


if __name__ == "__main__":
    main()
