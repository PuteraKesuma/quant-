"""FASE-30: PORTOFOLIO FINAL — memakai KODE TERVALIDASI USER, bukan tulisan ulang.

Fase-28 memakai reimplementasi ORB & ZREV buatan kami, dan hasilnya RUGI (ORB -$459).
Kode asli user (research/portfolio_audit.py + research/audit_live_strategies.py)
memberi hasil yang jauh berbeda: ORB +$612 (maxDD hanya -$123), ZREV +$2601.
Jadi yang salah adalah reimplementasi kami, bukan strateginya. Fase ini memakai
fungsi user apa adanya.

EMPAT SLEEVE:
  ZREV_xau   — kode user (Donchian always-in + gate EMA100), $1/point XAU
  ORB_nas    — kode user (NY ORB + DST open + trend50 + BE 0.5R + exit 20:00)
  ETERNA_xau — hasil riset kami (H1 Supertrend konservatif TP 1:4)
  RSI2_nas   — mean-reversion RSI(2) harian long-only

CATATAN KEJUJURAN soal Deflated Sharpe:
  ZREV/ORB/RSI2 = konfigurasi yang SUDAH ada sebelum sesi ini; kami tidak menyapunya.
  ETERNA = hasil pencarian ~1900 konfigurasi -> membawa bias seleksi berat.
  Maka DSR portofolio dilaporkan DUA KALI:
    (a) N=2  — hanya 2 skema bobot baku, mengasumsikan sleeve bebas bias
    (b) N=1900 — pembacaan konservatif yang menanggung bias seleksi eterna
  Angka (b) adalah yang boleh dipercaya kalau eterna ikut di dalam portofolio.

Jalankan: python research/portfolio_final.py
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "research"))

from portfolio_audit import zrev_dollars, nas_dollars      # KODE TERVALIDASI USER

RAW = r"C:\Quant\data\Level_0_Raw"
CAPITAL, COST, LOT = 1000.0, 0.50, 0.01


def load(sym, tf):
    con = duckdb.connect(rf"{RAW}\{sym}_1m.duckdb", read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts").resample(tf, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()


def atr_s(df, n):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(),
                    (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def supertrend(df, period, mult):
    a = atr_s(df, period)
    hl2 = (df["high"] + df["low"]) / 2.0
    up = (hl2 + mult * a).to_numpy(); lo = (hl2 - mult * a).to_numpy()
    c = df["close"].to_numpy(); n = len(df)
    fu = np.full(n, np.nan); fl = np.full(n, np.nan); d = np.ones(n, dtype=int)
    for i in range(1, n):
        if np.isnan(up[i]) or np.isnan(lo[i]):
            continue
        fu[i] = up[i] if (np.isnan(fu[i-1]) or up[i] < fu[i-1] or c[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = lo[i] if (np.isnan(fl[i-1]) or lo[i] > fl[i-1] or c[i-1] < fl[i-1]) else fl[i-1]
        if not np.isnan(fu[i-1]) and c[i] > fu[i]:
            d[i] = 1
        elif not np.isnan(fl[i-1]) and c[i] < fl[i]:
            d[i] = -1
        else:
            d[i] = d[i-1]
    return pd.Series(d, index=df.index)


def sleeve_eterna():
    h = load("XAUUSD", "1h")
    P, ME, MT, TPR, MIN = 16, 1.8, 3.8, 4.0, 0.30
    se, st = supertrend(h, P, ME), supertrend(h, P, MT)
    sd = se.where(se != se.shift(1)).shift(1).to_numpy(); td = st.shift(1).to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(P).min().shift(1).to_numpy()
    shi = h["high"].rolling(P).max().shift(1).to_numpy()
    pos = 0; entry = sl = tp = 0.0; ei = 0; out = []
    for i in range(1, len(h)):
        if pos != 0:
            hit = (sl if lo[i] <= sl else (tp if hi[i] >= tp else None)) if pos == 1 \
                  else (sl if hi[i] >= sl else (tp if lo[i] <= tp else None))
            if hit is not None:
                out.append((h.index[ei], (hit - entry) * pos * LOT * 100 - COST)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((h.index[ei], (o[i] - entry) * pos * LOT * 100 - COST)); pos = 0
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s:
            continue
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(o[i] - raw)
        if dist < MIN:
            continue
        pos, entry, ei = s, o[i], i
        sl = o[i] - dist if s == 1 else o[i] + dist
        tp = o[i] + TPR * dist if s == 1 else o[i] - TPR * dist
    return pd.Series([v for _, v in out], index=pd.DatetimeIndex([t for t, _ in out]))


def sleeve_rsi2():
    d1 = load("NAS100", "1D")
    c = d1["close"]; dlt = c.diff()
    up = dlt.clip(lower=0).rolling(2).mean(); dn = (-dlt.clip(upper=0)).rolling(2).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    s200 = c.rolling(200).mean(); s5 = c.rolling(5).mean()
    R, C, S200, S5, O = (rsi.to_numpy(), c.to_numpy(), s200.to_numpy(),
                         s5.to_numpy(), d1["open"].to_numpy())
    pos = 0; entry = 0.0; ei = 0; out = []
    for i in range(1, len(d1)):
        if pos == 0:
            if not np.isnan(R[i-1]) and not np.isnan(S200[i-1]) and R[i-1] < 10 and C[i-1] > S200[i-1]:
                pos, entry, ei = 1, O[i], i
        else:
            if not np.isnan(S5[i-1]) and C[i-1] > S5[i-1]:
                out.append((d1.index[ei], (O[i] - entry) * LOT * 10 - COST)); pos = 0
    return pd.Series([v for _, v in out], index=pd.DatetimeIndex([t for t, _ in out]))


def dsr(monthly, n_trials):
    r = np.asarray(monthly, dtype=float); n = len(r)
    if n < 12 or r.std(ddof=1) == 0:
        return np.nan, np.nan, np.nan
    sr = r.mean() / r.std(ddof=1)
    sk, ku = stats.skew(r), stats.kurtosis(r, fisher=False)
    e = np.euler_gamma
    sr0 = np.sqrt(1.0 / (n - 1)) * (
        (1 - e) * stats.norm.ppf(1 - 1.0 / n_trials) + e * stats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    den = np.sqrt(1 - sk * sr + (ku - 1) / 4.0 * sr ** 2)
    if den <= 0 or np.isnan(den):
        return sr, sr0, np.nan
    return sr, sr0, stats.norm.cdf((sr - sr0) * np.sqrt(n - 1) / den)


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
    st_ = mx = 0
    for v in m:
        st_ = st_ + 1 if v < 0 else 0
        mx = max(mx, st_)
    return {"sleeve": label, "bln": len(m), "CAGR%": round(100 * cagr, 1),
            "maxDD%": round(100 * dd, 1), "Sharpe": round(sh, 2),
            "Calmar": round(cagr / abs(dd), 2) if dd else np.nan,
            "hijau%": round(100 * (m > 0).mean()), "beruntun": mx}


def main():
    print("Membangun 4 sleeve (2 dari kode TERVALIDASI user) ...\n")
    sl = {}
    sl["ZREV_xau"] = zrev_dollars();  print(f"  ZREV_xau   {len(sl['ZREV_xau']):>5} trade  (kode user)", flush=True)
    sl["ORB_nas"] = nas_dollars();    print(f"  ORB_nas    {len(sl['ORB_nas']):>5} trade  (kode user)", flush=True)
    sl["ETERNA_xau"] = sleeve_eterna(); print(f"  ETERNA_xau {len(sl['ETERNA_xau']):>5} trade", flush=True)
    sl["RSI2_nas"] = sleeve_rsi2();   print(f"  RSI2_nas   {len(sl['RSI2_nas']):>5} trade", flush=True)

    for k in sl:
        if sl[k].index.tz is None:
            sl[k].index = sl[k].index.tz_localize("UTC")
    mon = pd.DataFrame({k: v.resample("ME").sum() for k, v in sl.items()}).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]

    print("\n" + "=" * 92)
    print("A. TIAP SLEEVE SENDIRIAN (modal $1000, lot 0.01)")
    print("=" * 92)
    print(pd.DataFrame([r for r in (metrics(mon[c], c) for c in mon.columns) if r]).to_string(index=False))

    print("\n" + "=" * 92)
    print("B. KORELASI PnL BULANAN")
    print("=" * 92)
    print(mon.corr().round(2).to_string())
    cm = mon.corr().values; off = cm[np.triu_indices_from(cm, k=1)]
    print(f"\n  rata-rata {off.mean():+.3f}   tertinggi {off.max():+.2f}   terendah {off.min():+.2f}")

    print("\n" + "=" * 92)
    print("C. PORTOFOLIO (2 skema bobot baku, TIDAK dioptimasi)")
    print("=" * 92)
    eq_w = pd.Series(1.0 / len(mon.columns), index=mon.columns)
    vol = mon.std(); iv_w = (1 / vol) / (1 / vol).sum()
    ports = {"setara": mon @ eq_w, "inverse-vol": mon @ iv_w}
    rows = [metrics(v, f"PORTFOLIO {k}") for k, v in ports.items()]
    rows += [metrics(mon[c], f"  (rujukan) {c}") for c in mon.columns]
    print(pd.DataFrame([r for r in rows if r]).to_string(index=False))
    print("\n  bobot inverse-vol: " + ", ".join(f"{k}={v:.0%}" for k, v in iv_w.items()))

    print("\n" + "=" * 92)
    print("D. DEFLATED SHARPE — dua pembacaan")
    print("=" * 92)
    for k, v in ports.items():
        for N, tag in ((2, "N=2 (asumsi sleeve bebas bias)"),
                       (1900, "N=1900 (menanggung bias seleksi eterna)")):
            s, s0, p = dsr((v / CAPITAL).values, N)
            von = "LOLOS" if p >= 0.95 else ("BATAS" if p >= 0.90 else "TIDAK LOLOS")
            print(f"  {k:<12} {tag:<44} DSR {p:.4f}  -> {von}")

    print("\n" + "=" * 92)
    print("E. TARGET: DD <=10%")
    print("=" * 92)
    print(f"  {'portofolio':<14}{'skala':>7}{'CAGR':>9}{'maxDD':>9}{'hijau%':>9}{'beruntun':>10}")
    for k, v in ports.items():
        b = metrics(v, k)
        if not b:
            continue
        for sc, tag in ((1.0, ""), (min(10.0 / abs(b["maxDD%"]), 1.0), " <- DD 10%")):
            r = metrics(v * sc, k)
            print(f"  {k:<14}{sc:>7.2f}{r['CAGR%']:>8.1f}%{r['maxDD%']:>8.1f}%"
                  f"{r['hijau%']:>8}%{r['beruntun']:>10}{tag}")
    # ---------------------------------------------------------------------------
    # F. PORTOFOLIO BERSIH — TANPA eterna.
    #    Dibuang atas DUA alasan yang masing-masing berdiri sendiri:
    #      1. REDUNDAN: korelasi 0.83 dengan ZREV (sama-sama trend-follow emas),
    #         dan inverse-vol hanya memberinya ~9% bobot.
    #      2. BIAS SELEKSI: eterna hasil pencarian ~1900 konfigurasi; tiga sleeve
    #         lain adalah konfigurasi yang SUDAH ADA sebelum sesi ini dan tidak
    #         pernah kami sapu.
    #    Karena itu DSR-nya boleh dihitung dengan N=2 (hanya 2 skema bobot).
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 92)
    print("F. PORTOFOLIO BERSIH — TANPA eterna (bebas bias seleksi)")
    print("=" * 92)
    m3 = mon.drop(columns=["ETERNA_xau"])
    cm3 = m3.corr()
    print(cm3.round(2).to_string())
    off3 = cm3.values[np.triu_indices_from(cm3.values, k=1)]
    print(f"\n  korelasi rata-rata {off3.mean():+.3f}  (tertinggi {off3.max():+.2f})")
    e3 = pd.Series(1.0 / len(m3.columns), index=m3.columns)
    v3 = m3.std(); i3 = (1 / v3) / (1 / v3).sum()
    p3 = {"setara": m3 @ e3, "inverse-vol": m3 @ i3}
    rows = [metrics(v, f"PORTFOLIO-3 {k}") for k, v in p3.items()]
    print("\n" + pd.DataFrame([r for r in rows if r]).to_string(index=False))
    print("\n  bobot inverse-vol: " + ", ".join(f"{k}={v:.0%}" for k, v in i3.items()))
    print("\n  Deflated Sharpe (N=2, sah karena tak ada sleeve hasil pencarian):")
    for k, v in p3.items():
        s, s0, p = dsr((v / CAPITAL).values, 2)
        von = "LOLOS" if p >= 0.95 else ("BATAS" if p >= 0.90 else "TIDAK LOLOS")
        print(f"    {k:<12} Sharpe {s*np.sqrt(12):+.2f}  ambang {s0*np.sqrt(12):+.2f}  "
              f"DSR {p:.4f}  -> {von}")
    print("\n  Distribusi bulanan (inverse-vol):")
    mm = p3["inverse-vol"]
    print("    " + "".join("+" if x > 0 else "-" for x in mm))
    st_ = mx = 0
    for x in mm:
        st_ = st_ + 1 if x < 0 else 0
        mx = max(mx, st_)
    print(f"    hijau {int((mm>0).sum())}/{len(mm)} ({100*(mm>0).mean():.0f}%)   "
          f"merah beruntun terpanjang {mx} bulan   bulan terburuk ${mm.min():+,.0f}")

    mon.to_csv(r"C:\Quant\_MONITOR\portfolio_final_monthly.csv")
    print("\nDisimpan: C:\\Quant\\_MONITOR\\portfolio_final_monthly.csv")


if __name__ == "__main__":
    main()
