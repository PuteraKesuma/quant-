"""UJI PEMBEDA: apakah SMC menambah apa-apa, atau cuma menunggangi rezim emas 2024-2026?

H4-B lolos uji dataran parameter (21/21 varian untung) dan mengalahkan beli-dan-tahan
secara risk-adjusted (42.6 vs 11.9 untung per poin drawdown). Tapi:

    2021-2023 : 52 trade, net  -$9.54   PF 0.97   <- TIGA TAHUN MENGHASILKAN NOL
    2024-2026 : 44 trade, net +$640.47  PF 2.30
    buang 5 trade terbaik dari 96 -> net -$46

Pola itu bisa berarti dua hal yang sangat berbeda:
  (a) mesin SMC (Order Block + FVG) betul-betul menemukan sesuatu, atau
  (b) rezim emas 2024-2026 begitu trending sehingga APA PUN yang long-bias
      dengan TP 1:2 akan mencetak uang.

Dataran parameter TIDAK bisa membedakan keduanya - strategi tren mana pun punya
dataran di rezim trending. Yang bisa membedakan: bandingkan dengan pembanding BODOH
yang tidak punya OB dan tidak punya FVG sama sekali, di periode yang sama persis,
dengan biaya yang sama persis.

TIGA PEMBANDING BODOH:
  1. BOS-saja      : masuk MARKET saat BOS, tanpa OB, tanpa FVG, tanpa limit
  2. BOS + limit   : masuk LIMIT di harga BOS retrace X%, tanpa OB, tanpa FVG
  3. long-saja     : BOS bullish saja (buang semua short) - menguji apakah
                     hasilnya sekadar beta ke emas

Kalau pembanding bodoh menghasilkan uang yang sebanding, mesin OB+FVG tidak menambah
apa-apa dan sleeve ini ditolak.

Ditambah dua uji portofolio (pelajaran ZREV: untung sendirian != berguna di buku):
  4. komposisi long vs short
  5. korelasi bulanan dengan eterna_xau + Calmar portofolio 2-sleeve vs 3-sleeve

Jalankan: python research/smc_vs_bodoh.py
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

BASE = dict(k=3, ob_lookback=10, expiry=12, rr=2.0, buffer_frac=0.10,
            pakai_fvg=True, pakai_sweep=False)


# ---------------------------------------------------------------- pembanding bodoh

def bodoh(h: pd.DataFrame, *, k=3, rr=2.0, sl_atr=1.5, atr_n=14,
          mode="market", retrace=0.30, expiry=12, hanya_long=False) -> pd.DataFrame:
    """BOS tanpa OB dan tanpa FVG. SL dari ATR, bukan dari struktur zona.

    mode "market": masuk di open bar berikutnya setelah BOS.
    mode "limit" : pasang limit di retrace X% dari leg, expiry sama seperti H4-B.
    """
    sh, sl_piv = swing_pivots(h, k)
    o = h["open"].to_numpy(); c = h["close"].to_numpy()
    hi = h["high"].to_numpy(); lo = h["low"].to_numpy()
    idx = h.index; n = len(h)

    tr = np.maximum(hi[1:] - lo[1:],
                    np.maximum(np.abs(hi[1:] - c[:-1]), np.abs(lo[1:] - c[:-1])))
    atr = np.concatenate([[np.nan], pd.Series(tr).rolling(atr_n).mean().to_numpy()])

    out = []
    pos = 0; entry = s = t_ = 0.0; ei = 0
    pend = None
    i_sh = i_sl = 0; last_sh = last_sl = None
    sh_ditembus = sl_ditembus = False

    for j in range(1, n):
        while i_sh < len(sh) and sh[i_sh][2] <= j:
            last_sh = sh[i_sh][1]; i_sh += 1; sh_ditembus = False
        while i_sl < len(sl_piv) and sl_piv[i_sl][2] <= j:
            last_sl = sl_piv[i_sl][1]; i_sl += 1; sl_ditembus = False

        if pos != 0:
            if pos == 1:
                if lo[j] <= s:
                    out.append((idx[ei], idx[j], 1, entry, s, "SL")); pos = 0
                elif hi[j] >= t_:
                    out.append((idx[ei], idx[j], 1, entry, t_, "TP")); pos = 0
            else:
                if hi[j] >= s:
                    out.append((idx[ei], idx[j], -1, entry, s, "SL")); pos = 0
                elif lo[j] <= t_:
                    out.append((idx[ei], idx[j], -1, entry, t_, "TP")); pos = 0

        if pend is not None and pos == 0:
            arah, px, ss, tt, exp_bar = pend
            kena = (lo[j] <= px) if arah == 1 else (hi[j] >= px)
            if kena:
                pos, entry, ei, s, t_ = arah, px, j, ss, tt; pend = None
            elif j >= exp_bar:
                pend = None
        elif pend is not None:
            pend = None

        arah = 0
        if last_sh is not None and not sh_ditembus and c[j] > last_sh:
            arah = 1; sh_ditembus = True
        elif last_sl is not None and not sl_ditembus and c[j] < last_sl:
            arah = -1; sl_ditembus = True
        if arah == 0 or (hanya_long and arah == -1):
            continue
        if pos != 0 or pend is not None or atr[j] != atr[j]:
            continue

        dist = sl_atr * atr[j]
        if dist <= 0:
            continue
        if mode == "market":
            if j + 1 >= n:
                continue
            px = o[j + 1]
            ss = px - dist if arah == 1 else px + dist
            tt = px + rr * dist if arah == 1 else px - rr * dist
            pos, entry, ei, s, t_ = arah, px, j + 1, ss, tt
        else:
            lvl = last_sh if arah == 1 else last_sl
            px = c[j] - retrace * abs(c[j] - lvl) if arah == 1 else c[j] + retrace * abs(c[j] - lvl)
            ss = px - dist if arah == 1 else px + dist
            tt = px + rr * dist if arah == 1 else px - rr * dist
            pend = (arah, px, ss, tt, j + expiry)

    t = pd.DataFrame(out, columns=["masuk", "keluar", "arah", "px_in", "px_out", "sebab"])
    if len(t) == 0:
        return t
    t["kotor"] = (t.px_out - t.px_in) * t.arah * LOT * 100
    t["malam"] = [malam(a, b) for a, b in zip(t.masuk, t.keluar)]
    t["swap"] = np.where(t.arah == 1, t.malam * SWAP_LONG, t.malam * SWAP_SHORT)
    t["pnl"] = t.kotor - SPREAD + t.swap
    return t


def belah(t: pd.DataFrame, label: str) -> None:
    if len(t) < 10:
        print(f"  {label:<26} n={len(t)} terlalu sedikit"); return
    a = t[t.masuk < "2024-01-01"]; b = t[t.masuk >= "2024-01-01"]
    w, l = t.pnl[t.pnl > 0].sum(), -t.pnl[t.pnl < 0].sum()
    print(f"  {label:<26} n={len(t):>4}  net ${t.pnl.sum():>8.2f}  PF {(w/l if l else 99):>5.2f}   "
          f"| 21-23 ${a.pnl.sum():>8.2f}  | 24-26 ${b.pnl.sum():>8.2f}")


def main():
    print("Membangun ...", flush=True)
    m1 = load_m1()
    h4 = tf(m1, "4h")

    smc = jalankan(h4, **BASE)
    print(f"  acuan H4-B: n={len(smc)} net ${smc.pnl.sum():.2f}")

    print("\n" + "=" * 104)
    print("1-3. SMC vs PEMBANDING BODOH (tanpa OB, tanpa FVG) — periode & biaya identik")
    print("=" * 104)
    print(f"  {'strategi':<26} {'':<7}{'':<18}{'':<8} |  pecah per periode")
    belah(smc, "H4-B (OB+BOS+FVG)")
    belah(bodoh(h4, mode="market"), "bodoh: BOS market")
    belah(bodoh(h4, mode="limit"), "bodoh: BOS limit 30%")
    belah(bodoh(h4, mode="limit", retrace=0.50), "bodoh: BOS limit 50%")
    belah(bodoh(h4, mode="market", hanya_long=True), "bodoh: BOS market LONG saja")
    belah(bodoh(h4, mode="limit", hanya_long=True), "bodoh: BOS limit LONG saja")

    print("\n" + "=" * 104)
    print("4. KOMPOSISI ARAH H4-B — apakah untungnya cuma beta ke emas?")
    print("=" * 104)
    for arah, nama in ((1, "LONG "), (-1, "SHORT")):
        g = smc[smc.arah == arah]
        if len(g) == 0:
            continue
        w, l = g.pnl[g.pnl > 0].sum(), -g.pnl[g.pnl < 0].sum()
        a = g[g.masuk < "2024-01-01"]; b = g[g.masuk >= "2024-01-01"]
        print(f"  {nama} n={len(g):>3} ({100*len(g)/len(smc):>3.0f}%)  net ${g.pnl.sum():>8.2f}  "
              f"PF {(w/l if l else 99):>5.2f}  winrate {100*(g.pnl>0).mean():>3.0f}%   "
              f"| 21-23 ${a.pnl.sum():>7.2f}  | 24-26 ${b.pnl.sum():>7.2f}")

    print("\n" + "=" * 104)
    print("5. NILAI PORTOFOLIO — untung sendirian tidak sama dengan berguna (pelajaran ZREV)")
    print("=" * 104)
    from blocking_akurat import load_h1, eterna_trades
    from portfolio_audit import nas_dollars

    et = eterna_trades(load_h1())
    et["malam"] = [malam(a, b) for a, b in zip(et.masuk, et.keluar)]
    et["pnl2"] = et.pnl + np.where(et.arah == 1, et.malam * SWAP_LONG, et.malam * SWAP_SHORT)
    orb = nas_dollars()
    if orb.index.tz is None:
        orb.index = orb.index.tz_localize("UTC")

    mo = pd.DataFrame({
        "ORB": orb.resample("ME").sum(),
        "ETERNA": et.set_index("masuk").pnl2.resample("ME").sum(),
        "SMC": smc.set_index("masuk").pnl.resample("ME").sum(),
    }).fillna(0.0)
    mo = mo.loc[(mo != 0).any(axis=1)]

    print("  korelasi imbal bulanan:")
    print(mo.corr().round(2).to_string())

    def ukur(kolom):
        p = mo[kolom].sum(axis=1)
        eq = CAPITAL + p.cumsum()
        dd = float(((eq - eq.cummax()) / eq.cummax()).min())
        yrs = len(p) / 12.0
        cagr = (eq.iloc[-1] / CAPITAL) ** (1 / yrs) - 1
        r = p / CAPITAL
        return {"CAGR%": round(100 * cagr, 1), "maxDD%": round(100 * dd, 1),
                "Calmar": round(cagr / abs(dd), 2),
                "Sharpe": round(r.mean() / r.std(ddof=1) * np.sqrt(12), 2),
                "hijau%": round(100 * (p > 0).mean())}

    print("\n  buku sekarang vs ditambah SMC:")
    r2 = ukur(["ORB", "ETERNA"]); r3 = ukur(["ORB", "ETERNA", "SMC"])
    print(f"  {'':<22}{'CAGR%':>9}{'maxDD%':>9}{'Calmar':>9}{'Sharpe':>9}{'hijau%':>9}")
    print(f"  {'ORB + ETERNA':<22}{r2['CAGR%']:>9}{r2['maxDD%']:>9}{r2['Calmar']:>9}"
          f"{r2['Sharpe']:>9}{r2['hijau%']:>9}")
    print(f"  {'+ SMC (3 sleeve)':<22}{r3['CAGR%']:>9}{r3['maxDD%']:>9}{r3['Calmar']:>9}"
          f"{r3['Sharpe']:>9}{r3['hijau%']:>9}")
    d = r3["Calmar"] - r2["Calmar"]
    print(f"\n  perubahan Calmar: {d:+.2f}  -> {'MEMBAIK' if d > 0 else 'MEMBURUK / DATAR'}")

    print("\n" + "=" * 104)


if __name__ == "__main__":
    main()
