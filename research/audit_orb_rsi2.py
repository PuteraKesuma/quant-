"""AUDIT KERAS: ORB (US100) dan RSI2 (US100) - dua sleeve pemegang 72% bobot portofolio.

Setelah ZREV dimatikan, ORB (0.03) + RSI2 (0.02) memegang mayoritas risiko. Kalau salah
satu dari keduanya sebenarnya rugi, seluruh portofolio jatuh - dan sampai sekarang belum
ada satu pun trade LIVE untuk membantah backtest.

Yang diuji di sini, bukan sekadar "berapa CAGR-nya":

  1. STABILITAS PER TAHUN   edge yang cuma hidup di 1-2 tahun adalah kebetulan
  2. KONSENTRASI            kalau 5 trade terbaik menyumbang semuanya, itu bukan edge
  3. DEFLATED SHARPE        dikoreksi jumlah percobaan pencarian (Bailey & Lopez de Prado)
  4. SENSITIVITAS BIAYA     backtest ORB memakai 2 poin/trade. Kalau 4 atau 6 bagaimana?
  5. SLIPPAGE ENTRY         ORB entry lewat STOP order; backtest menganggap fill PERSIS di
                            batas range. Gap dan slippage tidak pernah dimodelkan.
  6. PARITY LIVE vs BACKTEST  ini yang paling sering membunuh, dan yang paling jarang dicek.

TEMUAN PARITY YANG SUDAH DIKETAHUI SEBELUM SKRIP INI DIJALANKAN (dari membaca kode):
  RSI2 backtest (portfolio_final.sleeve_rsi2) memakai bar harian SELESAI: sinyal dari
  bar i-1, masuk di OPEN bar i. RSI2 live (research/reversal_sleeve.py) memanggil
  copy_rates_from_pos(D1, 0, 400) yang menyertakan BAR HARI INI YANG MASIH TERBENTUK,
  lalu memutuskan dari bar itu dan masuk di harga pasar saat itu juga (task 13:15 UTC).
  Tiga selisih sekaligus:
    (a) batas hari beda - backtest UTC 00:00, broker FBS 00:00 = 21:00 UTC
    (b) live memutuskan dari bar SEPARUH JADI, backtest dari bar selesai
    (c) live masuk tengah hari, backtest di open
  Skrip ini mengukur seberapa besar (a) dan (c) mengubah hasil.

Jalankan: python research/audit_orb_rsi2.py
"""
import warnings
warnings.filterwarnings("ignore")

import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(r"C:\Quant")
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "research"))

from audit_live_strategies import load_m1, to_d1, _nas_open_min

LOT_ORB, LOT_RSI = 0.03, 0.02
PT = 10.0            # US100: 1 lot = $10/poin -> 0.01 lot = $0.10/poin
CAPITAL = 1000.0


# ---------------------------------------------------------------- alat ukur
def dsr(r, n_trials):
    """Deflated Sharpe Ratio - probabilitas Sharpe ini bukan hasil keberuntungan pencarian."""
    r = np.asarray(r, float)
    n = len(r)
    if n < 12 or r.std(ddof=1) == 0:
        return np.nan, np.nan, np.nan
    sr = r.mean() / r.std(ddof=1)
    sk, ku = stats.skew(r), stats.kurtosis(r, fisher=False)
    e = np.euler_gamma
    sr0 = np.sqrt(1.0 / (n - 1)) * ((1 - e) * stats.norm.ppf(1 - 1.0 / n_trials)
                                    + e * stats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    den = np.sqrt(1 - sk * sr + (ku - 1) / 4.0 * sr ** 2)
    if den <= 0 or np.isnan(den):
        return sr, sr0, np.nan
    return sr, sr0, stats.norm.cdf((sr - sr0) * np.sqrt(n - 1) / den)


def ringkas(s, label, lot_unit):
    """s = seri PnL per-trade dalam dolar pada 0.01 lot."""
    s = s.sort_index()
    d = s * lot_unit
    eq = CAPITAL + d.cumsum()
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    w, l = d[d > 0].sum(), -d[d < 0].sum()
    m = d.resample("ME").sum()
    print(f"\n  {label}")
    print(f"    trade {len(d):>4}   net ${d.sum():+9.2f}   PF {w/l if l else float('inf'):.2f}   "
          f"winrate {100*(d>0).mean():.0f}%")
    print(f"    maxDD {100*dd:+.1f}%   bulan hijau {100*(m>0).mean():.0f}%   "
          f"bulan terburuk ${m.min():+.2f}   trade terburuk ${d.min():+.2f}")
    return d


def per_tahun(d, label):
    print(f"\n  {label} - PER TAHUN")
    print(f"    {'tahun':<8}{'trade':>7}{'net $':>11}{'PF':>7}{'winrate':>9}")
    for y, g in d.groupby(d.index.year):
        w, l = g[g > 0].sum(), -g[g < 0].sum()
        print(f"    {y:<8}{len(g):>7}{g.sum():>11.2f}{(w/l if l else 99):>7.2f}{100*(g>0).mean():>8.0f}%")
    tahun_rugi = sum(1 for _, g in d.groupby(d.index.year) if g.sum() < 0)
    n_tahun = d.index.year.nunique()
    print(f"    -> {tahun_rugi} dari {n_tahun} tahun RUGI")
    return tahun_rugi, n_tahun


def konsentrasi(d, label):
    s = d.sort_values(ascending=False)
    for k in (1, 5, 10):
        if len(s) > k:
            print(f"    {k:>2} trade terbaik menyumbang {100*s.head(k).sum()/d.sum():5.1f}% dari net")
    tanpa5 = d.sum() - s.head(5).sum()
    print(f"    tanpa 5 trade terbaik: net ${tanpa5:+.2f}  "
          f"({'MASIH UNTUNG' if tanpa5 > 0 else 'JADI RUGI - edge bertumpu pada segelintir trade'})")


# ---------------------------------------------------------------- ORB
def orb_trades(cost_pts=2.0, slip_pts=0.0):
    """Port setia dari portfolio_audit.nas_dollars, dengan biaya & slippage bisa diatur.

    slip_pts: harga fill entry LEBIH BURUK sekian poin dari batas range. Backtest asli
    menganggap 0 - fill persis di level. Order STOP nyata bisa lebih buruk saat gap.
    """
    nas = load_m1("NAS100")
    H, L, C = nas["high"].values, nas["low"].values, nas["close"].values
    mod = nas.index.hour.values * 60 + nas.index.minute.values
    dord = nas.index.normalize().asi8
    uniq, starts = np.unique(dord, return_index=True)
    starts = list(starts) + [len(nas)]
    d1 = to_d1(nas); dc = d1["close"]; pc = dc.shift(1); sma = dc.rolling(50).mean().shift(1)
    tmap = {ts.date(): (0 if (np.isnan(pc.loc[ts]) or np.isnan(sma.loc[ts]))
                        else (1 if pc.loc[ts] > sma.loc[ts] else -1)) for ts in d1.index}
    rows = []
    for di in range(len(uniq)):
        a, b = starts[di], starts[di + 1]
        day = nas.index[a].date()
        om = _nas_open_min(day); md = mod[a:b]; idx = np.arange(a, b)
        rm = (md >= om) & (md < om + 30)
        if rm.sum() < 15:
            continue
        ri = idx[rm]; oh = H[ri].max(); ol = L[ri].min(); size = oh - ol
        if size <= 0:
            continue
        pidx = idx[md >= om + 30]; ei = d = ent = None
        for i in pidx:
            if H[i] > oh: ei, d, ent = i, 1, oh + slip_pts; break
            if L[i] < ol: ei, d, ent = i, -1, ol - slip_pts; break
        if ei is None:
            continue
        td = tmap.get(day, 0)
        if td == 0 or (td > 0) != (d == 1):
            continue
        # SL/TP tetap diukur dari BATAS RANGE (seperti manager), bukan dari harga fill
        base = oh if d == 1 else ol
        sl = base - size if d == 1 else base + size
        tp = base + size if d == 1 else base - size
        cr = cost_pts / size
        armed = False; pnl = None
        for j in range(ei, b):
            if mod[j] >= 20 * 60:
                pnl = d * (C[j] - ent) / size - cr; break
            if d == 1:
                if not armed and (H[j] - ent) >= 0.5 * size: armed = True
                if armed and L[j] <= base: pnl = d * (base - ent) / size - cr; break
                if L[j] <= sl: pnl = d * (sl - ent) / size - cr; break
                if H[j] >= tp: pnl = d * (tp - ent) / size - cr; break
            else:
                if not armed and (ent - L[j]) >= 0.5 * size: armed = True
                if armed and H[j] >= base: pnl = d * (base - ent) / size - cr; break
                if H[j] >= sl: pnl = d * (sl - ent) / size - cr; break
                if L[j] <= tp: pnl = d * (tp - ent) / size - cr; break
        if pnl is None:
            pnl = d * (C[b - 1] - ent) / size - cr
        rows.append((nas.index[ei], pnl * size * (PT / 100.0)))   # -> $ pada 0.01 lot
    return pd.Series([p for _, p in rows], index=pd.DatetimeIndex([t for t, _ in rows]))


# ---------------------------------------------------------------- RSI2
def rsi2_trades(geser_jam=0, masuk="open_besok"):
    """Port dari portfolio_final.sleeve_rsi2, dengan dua knob untuk uji parity.

    geser_jam : geser M1 sebelum resample harian. 0 = hari UTC (seperti backtest asli),
                3 = hari BROKER FBS (00:00 broker = 21:00 UTC). Live memakai bar broker.
    masuk     : 'open_besok'  entry di open bar berikutnya  (backtest asli)
                'close_ini'   entry di close bar sinyal     (lookahead, sebagai pembanding
                              atas - kalau ini jauh lebih baik, edge-nya rapuh terhadap timing)
    """
    con = duckdb.connect(str(ROOT / "data" / "Level_0_Raw" / "NAS100_1m.duckdb"), read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True) + pd.Timedelta(hours=geser_jam)
    d1 = df.set_index("ts").resample("1D", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()

    c = d1["close"]; dlt = c.diff()
    up = dlt.clip(lower=0).rolling(2).mean(); dn = (-dlt.clip(upper=0)).rolling(2).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    s200 = c.rolling(200).mean(); s5 = c.rolling(5).mean()
    R, C_, S200, S5, O = (rsi.to_numpy(), c.to_numpy(), s200.to_numpy(),
                          s5.to_numpy(), d1["open"].to_numpy())
    pos = 0; entry = 0.0; ei = 0; out = []
    for i in range(1, len(d1)):
        if pos == 0:
            if not np.isnan(R[i-1]) and not np.isnan(S200[i-1]) and R[i-1] < 10 and C_[i-1] > S200[i-1]:
                pos, ei = 1, i
                entry = O[i] if masuk == "open_besok" else C_[i-1]
        else:
            if not np.isnan(S5[i-1]) and C_[i-1] > S5[i-1]:
                keluar = O[i] if masuk == "open_besok" else C_[i-1]
                out.append((d1.index[ei], (keluar - entry) * 0.01 * PT - 0.50, i - ei))
                pos = 0
    s = pd.Series([v for _, v, _ in out], index=pd.DatetimeIndex([t for t, _, _ in out]))
    tahan = pd.Series([h for _, _, h in out], index=s.index)
    return s, tahan


# ---------------------------------------------------------------- main
def main():
    print("=" * 100)
    print("AUDIT ORB + RSI2 (US100) - dua sleeve pemegang 72% bobot portofolio")
    print("=" * 100)

    # ================= ORB =================
    print("\n" + "#" * 100)
    print("# ORB 30 menit, sesi NY, gate SMA50 harian, RR 1:1, breakeven +0.5R  (lot 0.03)")
    print("#" * 100)
    o = orb_trades()
    do = ringkas(o, "ORB dasar (biaya 2 poin, tanpa slippage)", LOT_ORB / 0.01)
    ry, ny = per_tahun(do, "ORB")
    print()
    konsentrasi(do, "ORB")

    mo = do.resample("ME").sum()
    print("\n  ORB - DEFLATED SHARPE (bulanan)")
    for n in (1, 20, 100, 500):
        sr, sr0, p = dsr(mo / CAPITAL, n)
        tanda = "LULUS" if p > 0.95 else ("lemah" if p > 0.5 else "GAGAL")
        print(f"    N={n:<5} percobaan -> Sharpe {sr:.3f} vs ambang {sr0:.3f}   DSR {p:.4f}  {tanda}")

    print("\n  ORB - SENSITIVITAS BIAYA (backtest asli memakai 2 poin/trade)")
    for cp in (2.0, 4.0, 6.0, 8.0):
        s = orb_trades(cost_pts=cp) * (LOT_ORB / 0.01)
        eqq = CAPITAL + s.cumsum()
        print(f"    biaya {cp:.0f} poin -> net ${s.sum():+9.2f}   PF {s[s>0].sum()/-s[s<0].sum():.2f}   "
              f"maxDD {100*((eqq-eqq.cummax())/eqq.cummax()).min():+.1f}%")

    print("\n  ORB - SENSITIVITAS SLIPPAGE ENTRY (STOP order tidak selalu fill di level)")
    for sp in (0.0, 1.0, 2.0, 5.0):
        s = orb_trades(slip_pts=sp) * (LOT_ORB / 0.01)
        print(f"    slippage {sp:.0f} poin -> net ${s.sum():+9.2f}   "
              f"PF {s[s>0].sum()/-s[s<0].sum():.2f}")

    n2 = len(do) // 2
    h1, h2 = do.iloc[:n2], do.iloc[n2:]
    print(f"\n  ORB - PARUH PERTAMA vs KEDUA (uji kestabilan kasar)")
    print(f"    paruh 1 ({h1.index[0]:%Y-%m} s/d {h1.index[-1]:%Y-%m}) net ${h1.sum():+9.2f}  PF {h1[h1>0].sum()/-h1[h1<0].sum():.2f}")
    print(f"    paruh 2 ({h2.index[0]:%Y-%m} s/d {h2.index[-1]:%Y-%m}) net ${h2.sum():+9.2f}  PF {h2[h2>0].sum()/-h2[h2<0].sum():.2f}")

    # ================= RSI2 =================
    print("\n" + "#" * 100)
    print("# RSI2 mean-reversion, long-only, US100 harian, TANPA stop (lot 0.02)")
    print("#" * 100)
    r, tahan = rsi2_trades()
    dr = ringkas(r, "RSI2 dasar (hari UTC, entry open besok - persis backtest portofolio)", LOT_RSI / 0.01)
    per_tahun(dr, "RSI2")
    print()
    konsentrasi(dr, "RSI2")

    print(f"\n  RSI2 - LAMA TAHAN POSISI (tidak ada stop, jadi ini risiko sesungguhnya)")
    print(f"    rata-rata {tahan.mean():.1f} hari   median {tahan.median():.0f}   "
          f"TERLAMA {tahan.max():.0f} hari")
    tt = dr.idxmin()
    print(f"    trade terburuk ${dr.min():+.2f} mulai {tt:%Y-%m-%d}, ditahan {tahan.loc[tt]:.0f} hari")

    mr = dr.resample("ME").sum()
    print("\n  RSI2 - DEFLATED SHARPE (bulanan)")
    for n in (1, 20, 100, 500):
        sr, sr0, p = dsr(mr / CAPITAL, n)
        tanda = "LULUS" if p > 0.95 else ("lemah" if p > 0.5 else "GAGAL")
        print(f"    N={n:<5} percobaan -> Sharpe {sr:.3f} vs ambang {sr0:.3f}   DSR {p:.4f}  {tanda}")

    print("\n  RSI2 - PARITY: batas hari UTC (backtest) vs broker UTC+3 (yang dipakai live)")
    for gj, nm in ((0, "hari UTC   (backtest)"), (3, "hari BROKER (live)")):
        s, _ = rsi2_trades(geser_jam=gj)
        s = s * (LOT_RSI / 0.01)
        eqq = CAPITAL + s.cumsum()
        print(f"    {nm:<24} trade {len(s):>3}  net ${s.sum():+9.2f}  "
              f"PF {s[s>0].sum()/-s[s<0].sum():.2f}  maxDD {100*((eqq-eqq.cummax())/eqq.cummax()).min():+.1f}%")

    print("\n  RSI2 - PARITY: sensitivitas waktu masuk")
    for mk, nm in (("open_besok", "entry OPEN besok (backtest)"), ("close_ini", "entry CLOSE hari sinyal")):
        s, _ = rsi2_trades(masuk=mk)
        s = s * (LOT_RSI / 0.01)
        print(f"    {nm:<30} net ${s.sum():+9.2f}  PF {s[s>0].sum()/-s[s<0].sum():.2f}")

    print("\n" + "=" * 100)
    print("Angka di atas adalah bahan penilaian, bukan vonis. Baca bagian tahun rugi,")
    print("konsentrasi, dan DSR bersama-sama - satu angka bagus tidak menyelamatkan sleeve.")
    print("=" * 100)


if __name__ == "__main__":
    main()
