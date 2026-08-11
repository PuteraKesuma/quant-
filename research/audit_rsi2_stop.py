"""Lanjutan audit RSI2: dua hal yang belum terjawab dan keduanya soal UANG NYATA.

1. DISASTER STOP 5% TIDAK ADA DI BACKTEST.
   reversal_sleeve.py memasang SL di -5% dari harga masuk. portfolio_final.sleeve_rsi2
   tidak memodelkannya sama sekali - dia menahan sampai close > SMA5, seberapa pun
   dalamnya. Jadi backtest dan live BUKAN strategi yang sama. Berapa trade yang
   sebenarnya akan kena stop itu?

2. BUG RE-ENTRY SETELAH KENA STOP.
   reversal_sleeve.py menghitung target dari mesin-keadaan (inpos), lalu membandingkan
   dengan posisi NYATA di MT5:
       action = "OPEN LONG" kalau target LONG dan tidak ada posisi
   Kalau disaster stop kena, posisi hilang TAPI mesin-keadaan masih inpos=LONG (dia
   tidak tahu apa-apa soal stop). Jalan harian berikutnya melihat target LONG + posisi
   FLAT -> MEMBUKA LAGI. Bisa berulang tiap hari sampai close > SMA5.
   Skrip ini menghitung berapa kali pola itu akan terjadi.

3. PORTOFOLIO DENGAN RSI2 VERSI JUJUR (hari broker), supaya angka yang dipegang user
   bukan angka yang kelewat bagus.

Jalankan: python research/audit_rsi2_stop.py
"""
import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))

PT = 10.0
CAPITAL = 1000.0
STOP_PCT = 0.05


def d1_bars(geser_jam):
    con = duckdb.connect(str(ROOT / "data" / "Level_0_Raw" / "NAS100_1m.duckdb"), read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True) + pd.Timedelta(hours=geser_jam)
    return df.set_index("ts").resample("1D", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def jalankan(geser_jam, pakai_stop, re_entry):
    """pakai_stop: modelkan disaster stop -5%. re_entry: modelkan bug buka-lagi."""
    d1 = d1_bars(geser_jam)
    c = d1["close"]; dlt = c.diff()
    up = dlt.clip(lower=0).rolling(2).mean(); dn = (-dlt.clip(upper=0)).rolling(2).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    s200 = c.rolling(200).mean(); s5 = c.rolling(5).mean()
    R, C_, S200, S5 = rsi.to_numpy(), c.to_numpy(), s200.to_numpy(), s5.to_numpy()
    O, LO = d1["open"].to_numpy(), d1["low"].to_numpy()

    inpos = False          # mesin-keadaan (yang dipakai live untuk menentukan target)
    punya = False          # posisi NYATA di broker
    entry = 0.0
    out, kena_stop = [], 0
    for i in range(1, len(d1)):
        # --- keluar / stop pada bar berjalan ---
        if punya:
            sl = entry * (1 - STOP_PCT)
            if pakai_stop and LO[i] <= sl:
                out.append((d1.index[i], (sl - entry) * 0.01 * PT - 0.50, "STOP"))
                punya = False
                kena_stop += 1

        # --- mesin-keadaan diperbarui dari bar SELESAI i-1 ---
        keluar_sinyal = (not np.isnan(S5[i-1])) and C_[i-1] > S5[i-1]
        masuk_sinyal = ((not np.isnan(R[i-1])) and (not np.isnan(S200[i-1]))
                        and R[i-1] < 10 and C_[i-1] > S200[i-1])

        if inpos and keluar_sinyal:
            if punya:
                out.append((d1.index[i], (O[i] - entry) * 0.01 * PT - 0.50, "EXIT"))
                punya = False
            inpos = False
        elif (not inpos) and masuk_sinyal:
            inpos = True
            if not punya:
                punya, entry = True, O[i]
        elif inpos and (not punya) and re_entry:
            # BUG: target masih LONG, posisi hilang karena stop -> live membuka lagi
            punya, entry = True, O[i]

    s = pd.Series([v for _, v, _ in out], index=pd.DatetimeIndex([t for t, _, _ in out]))
    return s, kena_stop, [k for _, _, k in out]


def lapor(s, label, lot=0.02):
    d = (s * (lot / 0.01)).sort_index()
    eq = CAPITAL + d.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    pf = d[d > 0].sum() / -d[d < 0].sum() if (d < 0).any() else float("inf")
    print(f"  {label:<46} n={len(d):>3}  net ${d.sum():+8.2f}  PF {pf:.2f}  "
          f"maxDD {100*dd:+6.1f}%  terburuk ${d.min():+7.2f}")
    return d


def main():
    print("=" * 104)
    print("RSI2 - DISASTER STOP 5% DAN BUG RE-ENTRY (keduanya ADA di live, TIDAK ADA di backtest)")
    print("=" * 104)

    print("\nA. HARI UTC (dasar backtest portofolio)")
    a, st_a, _ = jalankan(0, pakai_stop=False, re_entry=False)
    lapor(a, "seperti backtest: tanpa stop, tanpa bug")
    b, st_b, kb = jalankan(0, pakai_stop=True, re_entry=False)
    lapor(b, f"+ disaster stop 5% ({st_b} trade kena stop)")
    cc, st_c, kc = jalankan(0, pakai_stop=True, re_entry=True)
    lapor(cc, f"+ stop DAN bug re-entry ({st_c} kali kena stop)")

    print("\nB. HARI BROKER UTC+3 (yang BENAR-BENAR dipakai live)")
    d, st_d, _ = jalankan(3, pakai_stop=False, re_entry=False)
    lapor(d, "hari broker, tanpa stop")
    e, st_e, _ = jalankan(3, pakai_stop=True, re_entry=False)
    lapor(e, f"hari broker + disaster stop ({st_e} kena stop)")
    f, st_f, kf = jalankan(3, pakai_stop=True, re_entry=True)
    dl = lapor(f, f"hari broker + stop + bug re-entry  <- PALING DEKAT KE LIVE")

    print("\n" + "=" * 104)
    print("C. SELISIH ANTARA YANG DIPERCAYA DAN YANG AKAN TERJADI")
    print("=" * 104)
    base = (a * 2).sum()
    live = (f * 2).sum()
    print(f"  Angka yang dipakai portofolio (hari UTC, tanpa stop) : ${base:+9.2f}")
    print(f"  Perkiraan terdekat ke perilaku live                  : ${live:+9.2f}")
    print(f"  Selisih                                              : ${live-base:+9.2f} "
          f"({100*(live-base)/abs(base):+.0f}%)")

    print("\n" + "=" * 104)
    print("D. PORTOFOLIO 3 SLEEVE DENGAN RSI2 VERSI JUJUR")
    print("=" * 104)
    from blocking_akurat import load_h1, eterna_trades
    from portfolio_audit import nas_dollars

    et = eterna_trades(load_h1()).set_index("masuk").pnl
    orb = nas_dollars()
    if orb.index.tz is None:
        orb.index = orb.index.tz_localize("UTC")

    def bln(s, unit):
        s = s.copy()
        if s.index.tz is None:
            s.index = s.index.tz_localize("UTC")
        return (s * unit).resample("ME").sum()

    mon = pd.DataFrame({
        "ORB": bln(orb, 3), "ETERNA": bln(et, 1),
        "RSI2_optimis": bln(a, 2), "RSI2_jujur": bln(f, 2),
    }).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]

    for nm, col in (("RSI2 versi backtest (optimis)", "RSI2_optimis"),
                    ("RSI2 versi jujur (mendekati live)", "RSI2_jujur")):
        p = mon["ORB"] + mon["ETERNA"] + mon[col]
        eq = CAPITAL + p.cumsum()
        dd = float(((eq - eq.cummax()) / eq.cummax()).min())
        yrs = len(p) / 12.0
        cagr = (eq.iloc[-1] / CAPITAL) ** (1 / yrs) - 1
        pr = p / CAPITAL
        sh = pr.mean() / pr.std(ddof=1) * np.sqrt(12)
        print(f"  {nm:<36} CAGR {100*cagr:5.1f}%  maxDD {100*dd:6.1f}%  "
              f"Calmar {cagr/abs(dd):5.2f}  Sharpe {sh:.2f}  hijau {100*(p>0).mean():.0f}%")

    print("\n" + "=" * 104)


if __name__ == "__main__":
    main()
