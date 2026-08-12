"""EDGE HUNT — 4 keluarga strategi diuji LINTAS 9 PASAR, bukan disapu di satu pasar.

TUJUAN: mencari sleeve KETIGA. Sharpe portofolio naik seakar jumlah edge independen
(2 sleeve Sharpe 1,30 -> 3 sleeve ~1,59, +22%). Itu tuas terbesar yang tersisa.

METODE — dan ini bagian yang menentukan sah atau tidaknya seluruh latihan:

  Menguji SATU aturan tetap di 9 pasar  = 1 percobaan, 9 sampel  -> MENGUATKAN bukti
  Menyapu 9 parameter di 1 pasar        = 9 percobaan, 1 sampel  -> MELEMAHKAN bukti

  Efek yang nyata muncul di beberapa pasar sekaligus. Efek yang noise muncul di satu.
  Jadi TIDAK ADA parameter yang disapu di sini. Tiap hipotesis punya satu setelan yang
  ditetapkan dari alasan, lalu dijalankan apa adanya di semua simbol.

  Deflated Sharpe dihitung dengan N=4 (empat hipotesis), bukan N besar. Itulah untungnya
  disiplin ini: ambangnya rendah karena kita memang tidak mencari-cari.

KRITERIA LULUS — ditulis SEBELUM dijalankan, tidak boleh diubah sesudahnya:
  1. untung di >= 6 dari 9 simbol            (bukan kebetulan satu pasar)
  2. untung di >= 4 dari 6 tahun (basket)    (bukan satu rezim)
  3. |korelasi| ke ORB dan ETERNA < 0,30     (kalau tidak, dia bukan diversifikasi)
  4. PF basket >= 1,15 setelah biaya
  Gagal salah satu = ditolak. Tidak ada penyetelan penyelamatan.

EMPAT HIPOTESIS (alasan dulu, baru aturan):

  H1 ACCELERATION — momentum dari momentum. Kalau laju gerak MENINGKAT, arus pesanan
     sedang searah dan cenderung berlanjut. Aturan H1: return 4 bar terakhir > return
     8 bar sebelumnya (per bar), dan keduanya searah -> ikut arah. Keluar 12 bar.

  H2 MEAN REVERSION intraday — FX terdokumentasi mean-revert di dalam hari. M15 di
     jendela London/NY: fade z>2 dari SMA20, keluar |z|<0.5 atau tutup sesi.
     (aturan yang sama dengan research/intraday_mr.py - sengaja dipakai ulang)

  H3 LONDON OPEN momentum — jam pertama London (07:00-08:00 UTC) menetapkan arah sesi
     karena likuiditas Eropa masuk sekaligus. Tembus range jam pertama -> ikut, tutup
     16:00 UTC. Struktural, bukan hasil mencari jam terbaik.

  H4 NEWS-DRIVEN — rilis data AS terjadwal jam 8:30 ET (12:30/13:30 UTC tergantung DST).
     Volatilitas melebar dan arahnya sering berlanjut. Tembus range 15 menit sebelum
     rilis, dalam 30 menit sesudahnya. Tidak butuh isi beritanya, cuma JADWALNYA.

Jalankan: python research/edge_hunt_4keluarga.py
"""
from __future__ import annotations

import warnings
warnings.filterwarnings("ignore")

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

RAW = Path(r"C:\Quant\data\Level_0_Raw")
SIMBOL = ["XAUUSD", "NAS100", "EURUSD", "GBPUSD", "USDJPY",
          "USDCHF", "AUDUSD", "USDCAD", "NZDUSD"]

# biaya per trade dalam satuan "fraksi dari ATR harian" - netral terhadap simbol,
# supaya emas dan EURUSD dibandingkan adil tanpa mengurus nilai pip masing-masing
BIAYA_ATR = 0.03
N_HIPOTESIS = 4


def load(sym: str) -> pd.DataFrame:
    con = duckdb.connect(str(RAW / f"{sym}_1m.duckdb"), read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def tf(m1: pd.DataFrame, rule: str) -> pd.DataFrame:
    return m1.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def atr_harian(m1: pd.DataFrame) -> pd.Series:
    d = tf(m1, "1D")
    tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(),
                    (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(20).mean().shift(1)


# ------------------------------------------------------------------ H1
def h1_acceleration(m1, atr_d):
    h = tf(m1, "1h")
    c = h["close"]
    r4 = (c - c.shift(4)) / 4.0
    r8 = (c.shift(4) - c.shift(12)) / 8.0
    arah = np.where((r4 > 0) & (r8 > 0) & (r4 > r8), 1,
                    np.where((r4 < 0) & (r8 < 0) & (r4 < r8), -1, 0))
    sig = pd.Series(arah, index=h.index).shift(1).fillna(0)     # bertindak di bar BERIKUTNYA
    keluar = c.shift(-12)
    pnl = (keluar - c) * sig
    a = atr_d.reindex(h.index, method="ffill")
    return (pnl / a).dropna()[sig.reindex(pnl.index).ne(0)] - BIAYA_ATR * 0  # biaya di bawah


def h1_trades(m1, atr_d):
    h = tf(m1, "1h")
    c = h["close"]
    r4 = (c - c.shift(4)) / 4.0
    r8 = (c.shift(4) - c.shift(12)) / 8.0
    arah = pd.Series(np.where((r4 > 0) & (r8 > 0) & (r4 > r8), 1,
                              np.where((r4 < 0) & (r8 < 0) & (r4 < r8), -1, 0)),
                     index=h.index).shift(1)
    a = atr_d.reindex(h.index, method="ffill")
    out = []
    i = 0
    idx = h.index
    arr = arah.to_numpy(); cc = c.to_numpy(); aa = a.to_numpy()
    while i < len(h) - 12:
        s = arr[i]
        if s and not np.isnan(s) and not np.isnan(aa[i]) and aa[i] > 0:
            r = (cc[i + 12] - cc[i]) * s / aa[i] - BIAYA_ATR
            out.append((idx[i], r))
            i += 12
        else:
            i += 1
    return pd.Series([v for _, v in out], index=pd.DatetimeIndex([t for t, _ in out]))


# ------------------------------------------------------------------ H2
def h2_mr_intraday(m1, atr_d):
    b = tf(m1, "15min")
    b = b[(b.index.hour >= 7) & (b.index.hour < 20)]
    c = b["close"]
    sma = c.rolling(20).mean()
    sd = c.rolling(20).std(ddof=0)
    z = ((c - sma) / sd.replace(0, np.nan)).shift(1)
    a = atr_d.reindex(b.index, method="ffill")
    cc = c.to_numpy(); zz = z.to_numpy(); aa = a.to_numpy()
    hari = b.index.date
    out = []
    pos = 0; e = 0.0; ei = 0
    for i in range(len(b)):
        if pos != 0:
            tutup_hari = (i == len(b) - 1) or (hari[i + 1] != hari[i])
            if (not np.isnan(zz[i]) and abs(zz[i]) < 0.5) or tutup_hari:
                if aa[ei] > 0:
                    out.append((b.index[ei], (cc[i] - e) * pos / aa[ei] - BIAYA_ATR))
                pos = 0
        if pos == 0 and not np.isnan(zz[i]) and not np.isnan(aa[i]) and aa[i] > 0:
            if zz[i] > 2.0:
                pos, e, ei = -1, cc[i], i
            elif zz[i] < -2.0:
                pos, e, ei = 1, cc[i], i
    return pd.Series([v for _, v in out], index=pd.DatetimeIndex([t for t, _ in out]))


# ------------------------------------------------------------------ H3
def h3_london(m1, atr_d):
    m = m1.copy()
    m["d"] = m.index.date
    out = []
    for d, g in m.groupby("d"):
        ib = g[(g.index.hour == 7)]
        if len(ib) < 30:
            continue
        hi, lo = ib["high"].max(), ib["low"].min()
        post = g[(g.index.hour >= 8) & (g.index.hour < 16)]
        if post.empty:
            continue
        a = atr_d.reindex([pd.Timestamp(d, tz="UTC")], method="ffill")
        av = float(a.iloc[0]) if len(a) and not np.isnan(a.iloc[0]) else np.nan
        if not av or np.isnan(av) or av <= 0:
            continue
        arah = 0; e = 0.0
        for ts, bar in post.iterrows():
            if bar["high"] > hi:
                arah, e = 1, hi; break
            if bar["low"] < lo:
                arah, e = -1, lo; break
        if arah == 0:
            continue
        out.append((post.index[0], (post["close"].iloc[-1] - e) * arah / av - BIAYA_ATR))
    return pd.Series([v for _, v in out], index=pd.DatetimeIndex([t for t, _ in out]))


# ------------------------------------------------------------------ H4
def _rilis_utc(d: dt.date) -> int:
    """8:30 ET -> jam UTC (12 saat DST, 13 saat waktu standar)."""
    et = dt.datetime(d.year, d.month, d.day, 12, tzinfo=ZoneInfo("America/New_York"))
    return 12 if et.dst() != dt.timedelta(0) else 13


def h4_news(m1, atr_d):
    m = m1.copy()
    m["d"] = m.index.date
    out = []
    for d, g in m.groupby("d"):
        if pd.Timestamp(d).dayofweek > 4:
            continue
        jam = _rilis_utc(d)
        pre = g[(g.index.hour == jam) & (g.index.minute >= 15) & (g.index.minute < 30)]
        post = g[(g.index.hour == jam) & (g.index.minute >= 30)]
        if len(pre) < 10 or len(post) < 20:
            continue
        hi, lo = pre["high"].max(), pre["low"].min()
        a = atr_d.reindex([pd.Timestamp(d, tz="UTC")], method="ffill")
        av = float(a.iloc[0]) if len(a) and not np.isnan(a.iloc[0]) else np.nan
        if not av or np.isnan(av) or av <= 0:
            continue
        arah = 0; e = 0.0
        for ts, bar in post.iterrows():
            if bar["high"] > hi:
                arah, e = 1, hi; break
            if bar["low"] < lo:
                arah, e = -1, lo; break
        if arah == 0:
            continue
        out.append((post.index[0], (post["close"].iloc[-1] - e) * arah / av - BIAYA_ATR))
    return pd.Series([v for _, v in out], index=pd.DatetimeIndex([t for t, _ in out]))


# ------------------------------------------------------------------
def stat(s: pd.Series) -> dict:
    if len(s) < 30:
        return {"n": len(s), "net": 0.0, "pf": 0.0, "sharpe": 0.0}
    w, l = s[s > 0].sum(), -s[s < 0].sum()
    return {"n": len(s), "net": float(s.sum()),
            "pf": float(w / l) if l > 0 else np.inf,
            "sharpe": float(s.mean() / s.std(ddof=1) * np.sqrt(len(s)))}


def dsr(r, n_trials):
    r = np.asarray(r, float); n = len(r)
    if n < 12 or r.std(ddof=1) == 0:
        return np.nan
    sr = r.mean() / r.std(ddof=1)
    sk, ku = stats.skew(r), stats.kurtosis(r, fisher=False)
    e = np.euler_gamma
    sr0 = np.sqrt(1.0 / (n - 1)) * ((1 - e) * stats.norm.ppf(1 - 1.0 / n_trials)
                                    + e * stats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    den = np.sqrt(1 - sk * sr + (ku - 1) / 4.0 * sr ** 2)
    if den <= 0 or np.isnan(den):
        return np.nan
    return float(stats.norm.cdf((sr - sr0) * np.sqrt(n - 1) / den))


def main():
    H = {"H1 acceleration": h1_trades, "H2 mean-rev intraday": h2_mr_intraday,
         "H3 london open": h3_london, "H4 news 8:30 ET": h4_news}
    hasil = {k: {} for k in H}

    for sym in SIMBOL:
        print(f"  {sym} ...", end="", flush=True)
        m1 = load(sym)
        a = atr_harian(m1)
        for nama, fn in H.items():
            try:
                hasil[nama][sym] = fn(m1, a)
            except Exception as ex:
                print(f" [{nama} gagal: {ex}]", end="")
                hasil[nama][sym] = pd.Series(dtype=float)
        print(" ok", flush=True)

    print("\n" + "=" * 104)
    print("A. TIAP HIPOTESIS DI 9 PASAR  (satuan: ATR harian per trade, biaya sudah dipotong)")
    print("=" * 104)
    ringkas = {}
    for nama in H:
        print(f"\n  {nama}")
        print(f"    {'simbol':<10}{'n':>7}{'net (ATR)':>12}{'PF':>7}{'Sharpe':>9}")
        untung = 0
        for sym in SIMBOL:
            s = stat(hasil[nama][sym])
            tanda = "+" if s["net"] > 0 else " "
            if s["net"] > 0:
                untung += 1
            print(f"    {sym:<10}{s['n']:>7}{s['net']:>12.2f}{s['pf']:>7.2f}{s['sharpe']:>9.2f} {tanda}")
        print(f"    -> untung di {untung} dari {len(SIMBOL)} pasar")
        ringkas[nama] = untung

    print("\n" + "=" * 104)
    print("B. BASKET (gabungan 9 pasar, bobot sama) vs KRITERIA YANG DITETAPKAN DI DEPAN")
    print("=" * 104)
    print(f"  {'hipotesis':<24}{'n':>7}{'net':>10}{'PF':>7}{'thn +':>8}{'pasar +':>9}{'DSR':>8}   vonis")
    basket = {}
    for nama in H:
        semua = pd.concat([hasil[nama][s] for s in SIMBOL]).sort_index()
        basket[nama] = semua
        s = stat(semua)
        thn = semua.groupby(semua.index.year).sum()
        thn_plus = int((thn > 0).sum())
        bln = semua.resample("ME").sum()
        d = dsr(bln, N_HIPOTESIS)
        lulus = (ringkas[nama] >= 6) and (thn_plus >= 4) and (s["pf"] >= 1.15)
        gag = []
        if ringkas[nama] < 6: gag.append(f"pasar {ringkas[nama]}/9")
        if thn_plus < 4: gag.append(f"tahun {thn_plus}/{len(thn)}")
        if s["pf"] < 1.15: gag.append(f"PF {s['pf']:.2f}")
        v = "LULUS" if lulus else "gagal: " + ", ".join(gag)
        print(f"  {nama:<24}{s['n']:>7}{s['net']:>10.1f}{s['pf']:>7.2f}"
              f"{thn_plus:>6}/{len(thn)}{ringkas[nama]:>8}/9{d if d==d else 0:>8.3f}   {v}")

    print("\n" + "=" * 104)
    print("C. KORELASI KE SLEEVE YANG SUDAH ADA - syaratnya |r| < 0,30")
    print("=" * 104)
    import sys
    sys.path.insert(0, r"C:\Quant"); sys.path.insert(0, r"C:\Quant\research")
    from blocking_akurat import load_h1, eterna_trades
    from portfolio_audit import nas_dollars
    et = eterna_trades(load_h1()).set_index("masuk").pnl
    orb = nas_dollars()
    if orb.index.tz is None:
        orb.index = orb.index.tz_localize("UTC")
    ref = pd.DataFrame({"ORB": orb.resample("ME").sum(),
                        "ETERNA": et.resample("ME").sum()}).fillna(0.0)
    for nama in H:
        b = basket[nama].resample("ME").sum()
        j = pd.concat([ref, b.rename("baru")], axis=1).fillna(0.0)
        print(f"  {nama:<24} vs ORB {j['ORB'].corr(j['baru']):+.3f}   "
              f"vs ETERNA {j['ETERNA'].corr(j['baru']):+.3f}")

    print("\n" + "=" * 104)
    print("Yang LULUS semua kriteria layak jadi kandidat sleeve ketiga - dan tetap harus")
    print("diuji ulang pada simbol yang bisa kita tradingkan di FBS sebelum dipasang.")
    print("=" * 104)


if __name__ == "__main__":
    main()
