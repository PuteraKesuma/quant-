"""3 SLEEVE (tanpa ZREV) vs 4 SLEEVE - dengan pemblokiran eterna yang DIHITUNG PER-TRADE.

KENAPA DIHITUNG ULANG (jangan pakai research/portfolio_3vs4.py untuk keputusan ini):
Skrip lama itu memperkirakan efek `_book_conflict` dengan menolkan eterna di bulan-bulan
yang PnL-nya searah zrev. Proxy itu cacat - dua strategi bisa sama-sama untung sebulan
sambil memegang arah BERLAWANAN di waktu yang berbeda. `blocking_akurat.py` sudah
membuktikan angka benarnya lewat simulasi per-entry: 53,4% diblokir, bukan ~83%.

SATU HAL YANG SKRIP LAMA LEWATKAN SAMA SEKALI, dan justru inti pertanyaannya:
mematikan zrev BUKAN cuma membuang PnL zrev. Zrev-lah yang memblokir 53,4% entry eterna
(920622 ada di governor.magics, 920627 tidak). Matikan zrev -> ETERNA DAPAT JATAH PENUH.
Jadi perbandingan yang jujur adalah:

  3 sleeve : ORB + RSI2 + ETERNA PENUH        (tanpa zrev, tanpa blokir)
  4 sleeve : ORB + RSI2 + ZREV + ETERNA SISA  (dengan zrev, eterna kena blokir)

Membandingkan "3 sleeve dengan eterna yang diblokir" adalah membandingkan dunia yang
tidak pernah ada, dan akan membuat zrev terlihat lebih baik daripada yang sebenarnya.

Sumber logika sengaja DIPINJAM, bukan ditulis ulang (pelajaran 2026-08-09: reimplementasi
ORB/ZREV sendiri menghasilkan 0 trade diam-diam karena resample harian menyisipkan akhir
pekan kosong):
  - eterna & zrev per-trade -> blocking_akurat.py  (yang dipakai untuk angka 53,4%)
  - ORB & RSI2 per-trade    -> portfolio_audit.py / portfolio_final.py milik user

Jalankan: python research/portfolio_3vs4_akurat.py
"""
import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))

from blocking_akurat import load_h1, eterna_trades, zrev_trades
from portfolio_final import sleeve_rsi2
from portfolio_audit import nas_dollars

CAPITAL = 1000.0
LOTS = {"ORB_nas": 3, "RSI2_nas": 2, "ETERNA_xau": 1, "ZREV_xau": 1}   # unit 0.01


def metrics(m, label):
    m = m.dropna()
    if len(m) < 12:
        return None
    eq = CAPITAL + m.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min()
    yrs = len(m) / 12.0
    cagr = (eq.iloc[-1] / CAPITAL) ** (1 / yrs) - 1
    mr = m / CAPITAL
    sh = mr.mean() / mr.std(ddof=1) * np.sqrt(12) if mr.std(ddof=1) > 0 else np.nan
    st = mx = 0
    for v in m:
        st = st + 1 if v < 0 else 0
        mx = max(mx, st)
    return {"portofolio": label, "bln": len(m), "CAGR%": round(100 * cagr, 1),
            "maxDD%": round(100 * dd, 1), "Sharpe": round(sh, 2),
            "Calmar": round(cagr / abs(dd), 2) if dd else np.nan,
            "hijau%": round(100 * (m > 0).mean()), "merah beruntun": mx,
            "bulan terburuk$": round(m.min())}


def main():
    print("Membangun sleeve ...", flush=True)
    h = load_h1()
    et = eterna_trades(h)
    zt = zrev_trades(h)

    # ---- blokir PER-ENTRY: zrev sedang pegang posisi searah saat eterna mau masuk ----
    z = zt.sort_values("masuk").reset_index(drop=True)
    blocked = []
    for r in et.itertuples():
        buka = z[(z.masuk <= r.masuk) & (z.keluar > r.masuk)]
        blocked.append(bool(len(buka[buka.arah == r.arah]) > 0))
    et["diblokir"] = blocked
    nb = int(et.diblokir.sum())
    print(f"  eterna : {len(et)} entry, {nb} diblokir ({100*nb/len(et):.1f}%)", flush=True)
    print(f"  zrev   : {len(zt)} trade", flush=True)

    et_penuh = et.set_index("masuk").pnl                       # tanpa zrev -> semua lolos
    et_sisa  = et[~et.diblokir].set_index("masuk").pnl         # dengan zrev -> sebagian hilang
    zr       = zt.set_index("masuk").pnl

    raw = {"ORB_nas": nas_dollars(), "RSI2_nas": sleeve_rsi2()}
    for k in raw:
        if raw[k].index.tz is None:
            raw[k].index = raw[k].index.tz_localize("UTC")
    print(f"  ORB    : {len(raw['ORB_nas'])} trade", flush=True)
    print(f"  RSI2   : {len(raw['RSI2_nas'])} trade", flush=True)

    def bulanan(s, unit):
        s = s.copy()
        if s.index.tz is None:
            s.index = s.index.tz_localize("UTC")
        return (s * unit).resample("ME").sum()

    orb  = bulanan(raw["ORB_nas"],  LOTS["ORB_nas"])
    rsi2 = bulanan(raw["RSI2_nas"], LOTS["RSI2_nas"])
    ep   = bulanan(et_penuh, LOTS["ETERNA_xau"])
    es   = bulanan(et_sisa,  LOTS["ETERNA_xau"])
    zv   = bulanan(zr,       LOTS["ZREV_xau"])

    mon = pd.DataFrame({"ORB": orb, "RSI2": rsi2, "ET_penuh": ep,
                        "ET_sisa": es, "ZREV": zv}).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]

    p3 = mon["ORB"] + mon["RSI2"] + mon["ET_penuh"]
    p4 = mon["ORB"] + mon["RSI2"] + mon["ET_sisa"] + mon["ZREV"]
    # varian: zrev mati DAN unit-nya diberikan ke eterna (0.01 -> 0.02)
    p3b = mon["ORB"] + mon["RSI2"] + mon["ET_penuh"] * 2

    print("\n" + "=" * 112)
    print("A. APA YANG SEBENARNYA DIPERTUKARKAN")
    print("=" * 112)
    hilang_blokir = mon["ET_penuh"].sum() - mon["ET_sisa"].sum()
    print(f"  PnL zrev sepanjang periode                  : ${mon['ZREV'].sum():+9.2f}")
    print(f"  PnL eterna yang HILANG karena diblokir zrev : ${-hilang_blokir:+9.2f}")
    print(f"  ------------------------------------------------------------")
    print(f"  Sumbangan BERSIH zrev                       : ${mon['ZREV'].sum() - hilang_blokir:+9.2f}")
    print("\n  Inilah yang dilewatkan skrip lama: zrev harus membayar sendiri BIAYA")
    print("  memblokir eterna. Kalau angka bersih di atas negatif, zrev merugikan")
    print("  portofolio walaupun PnL-nya sendiri positif.")

    print("\n" + "=" * 112)
    print("B. TIGA PILIHAN, DIUKUR SAMA")
    print("=" * 112)
    rows = [
        metrics(p3,  "3 sleeve  ORB.03 RSI2.02 ETERNA.01        (zrev MATI, eterna penuh)"),
        metrics(p3b, "3 sleeve+ ORB.03 RSI2.02 ETERNA.02        (zrev MATI, eterna dapat unitnya)"),
        metrics(p4,  "4 sleeve  ORB.03 RSI2.02 ETERNA.01 ZREV.01 (eterna diblokir 53%)"),
    ]
    print(pd.DataFrame([r for r in rows if r]).to_string(index=False))

    print("\n" + "=" * 112)
    print("C. TIAP SLEEVE SENDIRIAN (pada lot yang dipasang)")
    print("=" * 112)
    solo = [metrics(mon["ORB"], "ORB_nas 0.03"), metrics(mon["RSI2"], "RSI2_nas 0.02"),
            metrics(mon["ET_penuh"], "ETERNA 0.01 penuh"), metrics(mon["ET_sisa"], "ETERNA 0.01 sisa"),
            metrics(mon["ZREV"], "ZREV_xau 0.01")]
    print(pd.DataFrame([r for r in solo if r]).to_string(index=False))

    print("\n" + "=" * 112)
    print("D. KORELASI BULANAN")
    print("=" * 112)
    print(mon[["ORB", "RSI2", "ET_penuh", "ZREV"]].corr().round(2).to_string())

    print("\n" + "=" * 112)
    print("E. 2026 SAJA, BULAN PER BULAN (modal $1.000)")
    print("=" * 112)
    t = mon[mon.index >= pd.Timestamp("2026-01-01", tz="UTC")]
    if len(t):
        a3, a4 = CAPITAL, CAPITAL
        print(f"  {'Bulan':<10}{'3 sleeve':>12}{'equity':>12}   {'4 sleeve':>12}{'equity':>12}")
        print("  " + "-" * 62)
        for i in t.index:
            v3 = t.loc[i, "ORB"] + t.loc[i, "RSI2"] + t.loc[i, "ET_penuh"]
            v4 = t.loc[i, "ORB"] + t.loc[i, "RSI2"] + t.loc[i, "ET_sisa"] + t.loc[i, "ZREV"]
            a3 += v3; a4 += v4
            print(f"  {i:%b %Y}{'':<3}{v3:>12.2f}{a3:>12.2f}   {v4:>12.2f}{a4:>12.2f}")

    print("\n" + "=" * 112)
    print("VONIS")
    print("=" * 112)
    m3, m3b, m4 = metrics(p3, "3"), metrics(p3b, "3b"), metrics(p4, "4")
    for lab, mm in (("3 sleeve (zrev mati)     ", m3),
                    ("3 sleeve+ (eterna 0.02)  ", m3b),
                    ("4 sleeve (zrev hidup)    ", m4)):
        print(f"  {lab}: CAGR {mm['CAGR%']:>6.1f}%  maxDD {mm['maxDD%']:>6.1f}%  "
              f"Calmar {mm['Calmar']:>5.2f}  Sharpe {mm['Sharpe']:>5.2f}  "
              f"hijau {mm['hijau%']:>3}%  merah beruntun {mm['merah beruntun']}")
    print()
    if m4["Calmar"] > m3["Calmar"] * 1.10:
        print("  >> ZREV LAYAK dipertahankan: Calmar 4-sleeve >10% lebih baik dari 3-sleeve,")
        print("     SUDAH memperhitungkan biaya pemblokiran eterna.")
    else:
        print("  >> ZREV TIDAK memberi perbaikan berarti setelah biaya pemblokiran eterna")
        print("     diperhitungkan. Mematikannya bisa dibenarkan angka, bukan cuma selera.")
    print("     (ambang: Calmar 4-sleeve harus >10% lebih baik dari 3-sleeve)")


if __name__ == "__main__":
    main()
