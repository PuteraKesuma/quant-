"""POLA WAKTU MURNI di XAUUSD — mekanisme yang BUKAN tren, jadi berpeluang tidak
berkorelasi dengan eterna.

KENAPA ARAH INI:
Semua kandidat sleeve ketiga hari ini gugur dengan pola yang sama - mereka menangkap
TREN EMAS yang sudah ditangkap eterna. H1 acceleration setelah dikelola berkorelasi
+0,856 dengan eterna. Ruang "tren emas" sudah penuh.

Pola waktu berbeda secara mekanis. Kalau emas punya kecenderungan pada JAM tertentu,
sebabnya adalah arus terjadwal - fixing London (10:30 & 15:00 waktu London), pembukaan
dan penutupan Comex, permintaan fisik sesi Asia, rebalancing akhir bulan. Arus itu
terjadi terlepas dari sedang tren atau tidak, jadi hasilnya berpeluang ortogonal.

JEBAKAN YANG DIHINDARI, dan ini inti rancangannya:
Mengukur 24 jam lalu menradingkan yang terbaik = 24 percobaan tersembunyi. DSR akan
menghukumnya berat, dan hasilnya hampir pasti cocok-data. Jadi datanya DIPISAH:

    2021-2023  PERIODE PENEMUAN  - boleh melihat, boleh memilih pola terbaik
    2024-2026  PERIODE PENGUJIAN - tidak disentuh sampai polanya sudah dikunci

Memilih jam terbaik dari paruh pertama itu SAH, karena yang menghakimi adalah paruh
kedua yang belum pernah dilihat. Itu bedanya penemuan dengan cocok-data.

Ditambah TIGA hipotesis struktural yang ditetapkan dari teori (bukan dari melihat data),
diuji di seluruh periode sebagai tes tunggal masing-masing.

KRITERIA LULUS, ditulis sebelum dijalankan:
  1. tanda return SAMA di periode penemuan dan pengujian (polanya bertahan)
  2. PF periode PENGUJIAN >= 1,15
  3. maxDD <= -35% pada lot 0.01 modal $548
  4. Calmar portofolio ORB+ETERNA+ini LEBIH BAIK dari ORB+ETERNA
Gagal salah satu = ditolak.

Biaya: spread XAU nyata $0,39 x 1,5 = $0,585 per trade bolak-balik.

Jalankan: python research/xau_pola_waktu.py
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
from blocking_akurat import load_h1, eterna_trades
from portfolio_audit import nas_dollars

BIAYA = 0.39 * 1.5
CAPITAL = 548.19
PISAH = pd.Timestamp("2024-01-01", tz="UTC")


def port_metrik(m, modal=CAPITAL):
    eq = modal + m.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    yrs = len(m) / 12.0
    cagr = (eq.iloc[-1] / modal) ** (1 / yrs) - 1
    r = m / modal
    return {"CAGR%": 100 * cagr, "maxDD%": 100 * dd,
            "Calmar": cagr / abs(dd) if dd else np.nan,
            "Sharpe": r.mean() / r.std(ddof=1) * np.sqrt(12)}


def ringkas(s, label):
    if len(s) < 20:
        return None
    w, l = s[s > 0].sum(), -s[s < 0].sum()
    eq = CAPITAL + s.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    return {"pola": label, "n": len(s), "net$": round(s.sum(), 2),
            "PF": round((w / l) if l > 0 else 99, 2),
            "per trade": round(s.mean(), 3),
            "maxDD%": round(100 * dd, 1),
            "winrate%": round(100 * (s > 0).mean())}


def trade_jam(h1: pd.DataFrame, jam_masuk: int, tahan: int, arah: int) -> pd.Series:
    """Masuk di OPEN bar jam tertentu, keluar `tahan` jam kemudian. Satu trade/hari."""
    o = h1["open"]
    idx = h1.index
    masuk = h1[idx.hour == jam_masuk]
    out = []
    pos = {t: i for i, t in enumerate(idx)}
    ov = o.to_numpy()
    for t in masuk.index:
        i = pos[t]
        j = i + tahan
        if j >= len(idx):
            continue
        out.append((t, (ov[j] - ov[i]) * arah - BIAYA))
    return pd.Series([v for _, v in out], index=pd.DatetimeIndex([t for t, _ in out]))


def main():
    print("Memuat XAU ...", flush=True)
    m1 = EH.load("XAUUSD")
    h1 = EH.tf(m1, "1h")

    # ---------------- A. profil deskriptif ----------------
    r = (h1["close"] - h1["open"])
    df = pd.DataFrame({"r": r, "jam": h1.index.hour, "hari": h1.index.dayofweek},
                      index=h1.index)
    isamp = df[df.index < PISAH]
    osamp = df[df.index >= PISAH]

    print("\n" + "=" * 96)
    print("A. RETURN RATA-RATA PER JAM (dolar @0.01 lot, SEBELUM biaya) — deskriptif saja")
    print("=" * 96)
    print(f"  {'jam UTC':<9}{'2021-2023':>13}{'2024-2026':>13}{'tanda sama?':>14}{'n':>8}")
    gi = isamp.groupby("jam")["r"].mean()
    go = osamp.groupby("jam")["r"].mean()
    cnt = isamp.groupby("jam")["r"].count()
    stabil = []
    for j in range(24):
        a, b = gi.get(j, 0.0), go.get(j, 0.0)
        sama = "ya" if (a > 0) == (b > 0) and abs(a) > 0.01 else ""
        if sama:
            stabil.append(j)
        print(f"  {j:<9}{a:>13.4f}{b:>13.4f}{sama:>14}{cnt.get(j,0):>8}")
    print(f"\n  jam dengan tanda konsisten di kedua periode: {stabil}")
    print("  (ini BELUM strategi - baru menunjukkan mana yang layak diuji)")

    print("\n" + "=" * 96)
    print("B. RETURN PER HARI DALAM MINGGU (dolar @0.01 lot)")
    print("=" * 96)
    nm = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    hi = isamp.groupby("hari")["r"].sum()
    ho = osamp.groupby("hari")["r"].sum()
    print(f"  {'hari':<9}{'2021-2023':>13}{'2024-2026':>13}{'tanda sama?':>14}")
    for d in range(7):
        a, b = hi.get(d, 0.0), ho.get(d, 0.0)
        if a == 0 and b == 0:
            continue
        print(f"  {nm[d]:<9}{a:>13.2f}{b:>13.2f}{('ya' if (a>0)==(b>0) else ''):>14}")

    # ---------------- C. hipotesis struktural (dari teori) ----------------
    print("\n" + "=" * 96)
    print("C. TIGA HIPOTESIS STRUKTURAL — ditetapkan dari teori, bukan dari melihat data")
    print("=" * 96)
    struktur = [
        ("PM fix London 15:00 WIB-London (14:00 UTC), tahan 2j, LONG", 14, 2, 1),
        ("Comex tutup 18:30 UTC -> masuk 18:00, tahan 3j, LONG", 18, 3, 1),
        ("Sesi Asia 00:00 UTC, tahan 7j, LONG", 0, 7, 1),
    ]
    rows = []
    for lab, jm, th, ar in struktur:
        s = trade_jam(h1, jm, th, ar)
        rr = ringkas(s, lab)
        if rr:
            rows.append(rr)
    print(pd.DataFrame(rows).to_string(index=False))

    # ---------------- D. penemuan lalu pengujian ----------------
    print("\n" + "=" * 96)
    print("D. PENEMUAN (2021-2023) lalu PENGUJIAN (2024-2026)")
    print("=" * 96)
    print("  Cari jam+arah+lama-tahan terbaik HANYA dari 2021-2023, lalu kunci dan uji.")
    # ---------------------------------------------------------------------------
    #  JAM YANG DIKECUALIKAN - pelajaran mahal 2026-08-11.
    #  Pencarian pertama menemukan "masuk 21:00 UTC, tahan 8 jam, LONG" yang LOLOS
    #  KEEMPAT kriteria: tanda konsisten, PF out-of-sample 1,69, maxDD -29%, dan
    #  Calmar portofolio 1,94 -> 3,37. Terlihat sempurna.
    #
    #  Lalu ketahuan: FBS MENUTUP XAUUSD jam 21:00-22:00 UTC untuk jeda harian.
    #  Nol tick selama 10 hari pengecekan. Data Dukascopy punya 16,4 bar/jam di situ
    #  (jam lain 49) - sisa print tipis dari batas sesi feed lain. Yang ditangkap
    #  "pola" itu adalah GAP melintasi jeda harian, dan order tidak mungkin dipasang
    #  saat pasar tutup.
    #
    #  Kriteria statistik tidak cukup. Harus ada pertanyaan terakhir: APAKAH TRADE INI
    #  BISA BENAR-BENAR TERJADI? Jam 20 dan 22 juga dikecualikan karena likuiditasnya
    #  tinggal sepertiga (36k dan 45k tick vs 100k+ di jam normal) - spread di situ
    #  tidak terwakili oleh $0,39 yang diukur siang hari.
    # ---------------------------------------------------------------------------
    JAM_TERTUTUP = {20, 21, 22}

    kandidat = []
    for jm in range(24):
        if jm in JAM_TERTUTUP:
            continue
        for th in (1, 2, 3, 4, 6, 8):
            for ar in (1, -1):
                s = trade_jam(h1, jm, th, ar)
                si = s[s.index < PISAH]
                if len(si) < 200:
                    continue
                w, l = si[si > 0].sum(), -si[si < 0].sum()
                pf = (w / l) if l > 0 else 99
                kandidat.append((pf, si.sum(), jm, th, ar))
    kandidat.sort(reverse=True)
    print(f"\n  5 terbaik di periode PENEMUAN saja:")
    print(f"    {'jam':>4}{'tahan':>7}{'arah':>6}{'PF is':>8}{'net is$':>10}")
    for pf, net, jm, th, ar in kandidat[:5]:
        print(f"    {jm:>4}{th:>7}{'LONG' if ar==1 else 'SHORT':>6}{pf:>8.2f}{net:>10.2f}")

    pf, net, jm, th, ar = kandidat[0]
    s = trade_jam(h1, jm, th, ar)
    si, so = s[s.index < PISAH], s[s.index >= PISAH]
    print(f"\n  POLA DIKUNCI: masuk jam {jm} UTC, tahan {th} jam, "
          f"{'LONG' if ar==1 else 'SHORT'}")
    print(f"    {'periode':<22}{'n':>6}{'net$':>10}{'PF':>7}{'winrate':>9}")
    for lab, x in (("PENEMUAN 2021-2023", si), ("PENGUJIAN 2024-2026", so)):
        w, l = x[x > 0].sum(), -x[x < 0].sum()
        print(f"    {lab:<22}{len(x):>6}{x.sum():>10.2f}"
              f"{((w/l) if l>0 else 99):>7.2f}{100*(x>0).mean():>8.0f}%")

    # ---------------- E. vonis + portofolio ----------------
    print("\n" + "=" * 96)
    print("E. VONIS")
    print("=" * 96)
    w, l = so[so > 0].sum(), -so[so < 0].sum()
    pf_oos = (w / l) if l > 0 else 99
    eq = CAPITAL + s.cumsum()
    dd = 100 * float(((eq - eq.cummax()) / eq.cummax()).min())
    tanda_sama = (si.sum() > 0) == (so.sum() > 0)

    et = eterna_trades(load_h1()).set_index("masuk").pnl
    orb = nas_dollars()
    if orb.index.tz is None:
        orb.index = orb.index.tz_localize("UTC")
    mon = pd.DataFrame({"ORB": orb.resample("ME").sum(),
                        "ETERNA": et.resample("ME").sum(),
                        "WAKTU": s.resample("ME").sum()}).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]
    a2 = port_metrik(mon["ORB"] + mon["ETERNA"])
    a3 = port_metrik(mon["ORB"] + mon["ETERNA"] + mon["WAKTU"])

    cek = [("tanda sama di kedua periode", tanda_sama, f"{si.sum():+.0f} / {so.sum():+.0f}"),
           ("PF pengujian >= 1.15", pf_oos >= 1.15, f"{pf_oos:.2f}"),
           ("maxDD >= -35%", dd >= -35, f"{dd:.1f}%"),
           ("Calmar portofolio membaik", a3["Calmar"] > a2["Calmar"],
            f"{a2['Calmar']:.2f} -> {a3['Calmar']:.2f}")]
    for nama, ok, nilai in cek:
        print(f"  [{'v' if ok else 'X'}] {nama:<32} {nilai}")
    print(f"\n  korelasi ke ETERNA {mon['WAKTU'].corr(mon['ETERNA']):+.3f}   "
          f"ke ORB {mon['WAKTU'].corr(mon['ORB']):+.3f}")
    print(f"\n  >> {'LULUS SEMUA' if all(o for _,o,_ in cek) else 'DITOLAK'}")
    print("=" * 96)


if __name__ == "__main__":
    main()
