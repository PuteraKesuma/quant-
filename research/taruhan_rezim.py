"""Kalau memang mau bertaruh rezim tren emas berlanjut — mana cara terbaiknya?

KEPUTUSAN USER: pasang kandidat yang kuat di 2025-2026, sadar bahwa dia bergantung
rezim. Itu keputusan yang sah — bertaruh rezim berlanjut adalah pandangan pasar,
bukan kesalahan statistik. Tugasku bukan menghalangi, tapi memastikan taruhannya
dipasang lewat cara yang paling efisien.

PERTANYAAN YANG SEBENARNYA:
User SUDAH punya taruhan tren emas — eterna. H1 acceleration berkorelasi +0,856
dengannya. Menambah H1 bukan menambah taruhan BARU; itu memperbesar taruhan yang
SAMA, tapi lewat kode baru dengan mode kegagalan baru.

Kalau tujuannya menambah eksposur ke rezim itu, ada cara yang jauh lebih sederhana:
naikkan lot eterna. Skrip ini membandingkan tiga cara memasang taruhan yang sama:

  A. ORB 0.01 + ETERNA 0.01                 (sekarang)
  B. ORB 0.01 + ETERNA 0.01 + H1 0.01       (tambah sleeve baru)
  C. ORB 0.01 + ETERNA 0.02                 (naikkan yang sudah ada)

B dan C sama-sama menambah eksposur tren emas. Kalau C setara atau lebih baik, maka
B cuma menambah kerumitan tanpa imbalan: satu proses lagi yang bisa mati diam-diam,
satu parameter lagi yang bisa salah, satu sumber bug lagi.

Diuji di DUA jendela:
  2021-2026  seluruh siklus       (apa yang terjadi kalau rezim berbalik)
  2025-2026  rezim yang ditaruhkan (apa yang terjadi kalau rezim bertahan)

Jalankan: python research/taruhan_rezim.py
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
import edge_hunt_4keluarga as EH
from h1_xau_dikelola import jalankan as h1_jalankan
from blocking_akurat import load_h1, eterna_trades
from portfolio_audit import nas_dollars

CAPITAL = 548.19


def metrik(m, modal=CAPITAL):
    if len(m) < 6:
        return None
    eq = modal + m.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yrs = len(m) / 12.0
    cagr = (eq.iloc[-1] / modal) ** (1 / yrs) - 1
    r = m / modal
    return {"CAGR%": round(100 * cagr, 1), "maxDD%": round(100 * dd, 1),
            "Calmar": round(cagr / abs(dd), 2) if dd else np.nan,
            "Sharpe": round(r.mean() / r.std(ddof=1) * np.sqrt(12), 2),
            "hijau%": round(100 * (m > 0).mean()),
            "terburuk$": round(m.min(), 2), "akhir$": round(eq.iloc[-1], 2)}


def main():
    print("Membangun ...", flush=True)
    m1 = EH.load("XAUUSD")
    h = EH.tf(m1, "1h")
    h1s = h1_jalankan(h, "V2")                       # varian terbaik yang sudah diuji

    et = eterna_trades(load_h1()).set_index("masuk").pnl
    orb = nas_dollars()
    if orb.index.tz is None:
        orb.index = orb.index.tz_localize("UTC")

    mon = pd.DataFrame({"ORB": orb.resample("ME").sum(),
                        "ET": et.resample("ME").sum(),
                        "H1": h1s.resample("ME").sum()}).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]

    opsi = {
        "A  ORB.01 + ETERNA.01  (sekarang)":      mon["ORB"] + mon["ET"],
        "B  + H1 sleeve baru .01":                 mon["ORB"] + mon["ET"] + mon["H1"],
        "C  ORB.01 + ETERNA.02  (naikkan lot)":    mon["ORB"] + mon["ET"] * 2,
    }

    for lab, a, b in (("SELURUH SIKLUS 2021-2026 — kalau rezim BERBALIK", "2021-01-01", "2027-01-01"),
                      ("REZIM YANG DITARUHKAN 2025-2026 — kalau rezim BERTAHAN", "2025-01-01", "2027-01-01")):
        print("\n" + "=" * 104)
        print(lab)
        print("=" * 104)
        rows = []
        for nm, p in opsi.items():
            q = p[(p.index >= pd.Timestamp(a, tz="UTC")) & (p.index < pd.Timestamp(b, tz="UTC"))]
            r = metrik(q)
            if r:
                rows.append({"opsi": nm, **r})
        print(pd.DataFrame(rows).to_string(index=False))

    print("\n" + "=" * 104)
    print("KORELASI — kenapa B dan C sebenarnya taruhan yang sama")
    print("=" * 104)
    print(f"  H1 vs ETERNA  {mon['H1'].corr(mon['ET']):+.3f}")
    print(f"  H1 vs ORB     {mon['H1'].corr(mon['ORB']):+.3f}")
    print("\n  Korelasi 0,8+ berarti H1 dan eterna naik-turun hampir bersamaan. Menambah H1")
    print("  memberi eksposur yang hampir sama dengan menggandakan eterna - tapi dengan")
    print("  satu proses lagi yang bisa mati diam-diam, satu parameter lagi yang bisa salah,")
    print("  dan satu sumber bug lagi.")

    print("\n" + "=" * 104)
    print("YANG HARUS DISADARI SEBELUM MEMILIH")
    print("=" * 104)
    print("  Baik B maupun C MEMPERBESAR taruhan pada rezim yang sama. Kalau emas berhenti")
    print("  tren atau berbalik turun, kerugiannya juga berlipat - lihat baris SELURUH SIKLUS.")
    print("  Portofolio sekarang (A) sudah 85% risikonya di eterna; B dan C menaikkannya lagi.")
    print("=" * 104)


if __name__ == "__main__":
    main()
