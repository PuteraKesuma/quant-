"""ETERNA fase-27: AUDIT INSTITUSIONAL (standar komite risiko hedge fund).

Target user: maxDD 10% DENGAN return tiap bulan. Audit ini menguji apakah itu mungkin,
dan mengoreksi SEMUA angka sebelumnya terhadap jumlah percobaan yang sudah dilakukan.

BAGIAN A — DEFLATED SHARPE RATIO (Bailey & Lopez de Prado 2014)
  Ini uji terpenting dan belum pernah dilakukan. Kami sudah menguji ~1900 konfigurasi.
  Mencari sebanyak itu hampir PASTI menghasilkan Sharpe tinggi secara kebetulan.
  DSR menjawab: setelah dikoreksi terhadap N percobaan, skew, dan kurtosis, berapa
  probabilitas Sharpe sejatinya > 0? Di bawah 0.95 = tidak lolos komite.

BAGIAN B — METRIK RISIKO LENGKAP
  Sharpe, Sortino, Calmar, Ulcer Index, VaR/CVaR 95, skew, kurtosis, tail ratio,
  anatomi drawdown (kedalaman, durasi, waktu pulih), rolling 12 bulan.

BAGIAN C — APAKAH TARGET USER MUNGKIN?
  Berapa leverage/lot yang membuat DD = 10%, dan berapa return yang tersisa?
  Berapa persen bulan hijau pada tiap tingkat itu? Ada trade-off yang tak bisa dihindari.

BAGIAN D — VONIS

Jalankan: python research/eterna_audit_institusional.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.01, 100.0, 0.50
CAPITAL, MIN_SL = 1000.0, 0.30
P, MULT_E, MULT_T, TP_R = 16, 1.8, 3.8, 4.0
N_TRIALS = 1900          # jumlah konfigurasi yang sudah diuji sepanjang riset ini


def load_h1():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts").resample("1h", label="left", closed="left").agg(
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


def run(h):
    st_e, st_t = supertrend(h, P, MULT_E), supertrend(h, P, MULT_T)
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(P).min().shift(1).to_numpy()
    shi = h["high"].rolling(P).max().shift(1).to_numpy()
    pos = 0; entry = sl = tp = 0.0; ei = 0; out = []
    for i in range(1, len(h)):
        if pos != 0:
            hit = None
            if pos == 1:
                hit = sl if lo[i] <= sl else (tp if hi[i] >= tp else None)
            else:
                hit = sl if hi[i] >= sl else (tp if lo[i] <= tp else None)
            if hit is not None:
                out.append((h.index[ei], h.index[i], pos, entry, hit)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((h.index[ei], h.index[i], pos, entry, o[i])); pos = 0
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s:
            continue
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(o[i] - raw)
        if dist < MIN_SL:
            continue
        pos, entry, ei = s, o[i], i
        sl = o[i] - dist if s == 1 else o[i] + dist
        tp = o[i] + TP_R * dist if s == 1 else o[i] - TP_R * dist
    t = pd.DataFrame(out, columns=["masuk", "keluar", "arah", "px_in", "px_out"])
    t["pnl"] = (t.px_out - t.px_in) * t.arah * LOT * CONTRACT - COST
    return t.set_index("masuk")


def dsr(returns, n_trials):
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014).

    Mengoreksi Sharpe terhadap: (a) jumlah percobaan N, (b) skew, (c) kurtosis,
    (d) panjang sampel. Mengembalikan probabilitas bahwa Sharpe SEJATI > 0.
    """
    r = np.asarray(returns, dtype=float)
    n = len(r)
    sr = r.mean() / r.std(ddof=1)
    sk = stats.skew(r)
    ku = stats.kurtosis(r, fisher=False)
    # Sharpe ambang yang DIHARAPKAN muncul dari N percobaan acak
    e = np.euler_gamma
    sr0 = np.sqrt(1.0 / (n - 1)) * (
        (1 - e) * stats.norm.ppf(1 - 1.0 / n_trials) + e * stats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    denom = np.sqrt(1 - sk * sr + (ku - 1) / 4.0 * sr ** 2)
    if denom <= 0 or np.isnan(denom):
        return sr, sr0, np.nan
    z = (sr - sr0) * np.sqrt(n - 1) / denom
    return sr, sr0, stats.norm.cdf(z)


def dd_anatomy(eq):
    peak = eq.cummax()
    dd = (eq - peak) / peak
    out = []
    in_dd = False
    for i in range(len(eq)):
        if dd.iloc[i] < -1e-9 and not in_dd:
            in_dd, start, trough, tval = True, eq.index[i], eq.index[i], dd.iloc[i]
        elif in_dd:
            if dd.iloc[i] < tval:
                tval, trough = dd.iloc[i], eq.index[i]
            if dd.iloc[i] >= -1e-9:
                out.append((start, trough, eq.index[i], tval))
                in_dd = False
    if in_dd:
        out.append((start, trough, None, tval))
    return pd.DataFrame(out, columns=["mulai", "terdalam", "pulih", "dd"])


def main():
    h = load_h1()
    t = run(h)
    eq = CAPITAL + t.pnl.cumsum()
    m = t.pnl.resample("ME").sum(); m = m[m != 0]
    mr = m / CAPITAL                       # return bulanan (fraksi modal)
    yrs = (t.index[-1] - t.index[0]).days / 365.25

    print("=" * 100)
    print("AUDIT INSTITUSIONAL — ETERNA (H1 ATR16 x1.8, gate x3.8, TP 1:4)")
    print(f"Periode {t.index[0]:%Y-%m-%d} .. {t.index[-1]:%Y-%m-%d}  ({yrs:.1f} tahun, "
          f"{len(t)} trade, {len(m)} bulan)")
    print("=" * 100)

    # ---------------- A. DEFLATED SHARPE ----------------
    print("\nBAGIAN A — DEFLATED SHARPE RATIO (uji paling menentukan)")
    print("-" * 100)
    sr_m, sr0, p = dsr(mr.values, N_TRIALS)
    ann = sr_m * np.sqrt(12)
    print(f"  Sharpe bulanan (mentah)       : {sr_m:+.3f}   -> disetahunkan {ann:+.2f}")
    print(f"  Skew {stats.skew(mr):+.2f}   Kurtosis {stats.kurtosis(mr, fisher=False):.2f}"
          f"   n={len(mr)} bulan")
    print(f"  Ambang Sharpe dari {N_TRIALS} percobaan acak : {sr0:+.3f}  "
          f"(disetahunkan {sr0*np.sqrt(12):+.2f})")
    print(f"  >> DEFLATED SHARPE (P[Sharpe sejati > 0]) : {p:.4f}")
    if p >= 0.95:
        print("     LOLOS — edge bertahan setelah dikoreksi terhadap jumlah percobaan.")
    elif p >= 0.90:
        print("     BATAS — di bawah ambang komite (0.95). Perlu bukti out-of-sample.")
    else:
        print("     TIDAK LOLOS — Sharpe ini tidak dapat dibedakan dari hasil pencarian acak.")

    # ---------------- B. METRIK RISIKO ----------------
    print("\nBAGIAN B — METRIK RISIKO LENGKAP")
    print("-" * 100)
    peak = eq.cummax(); ddser = (eq - peak) / peak
    mdd = ddser.min()
    downside = mr[mr < 0]
    sortino = mr.mean() / downside.std(ddof=1) * np.sqrt(12) if len(downside) > 1 else np.nan
    cagr = (eq.iloc[-1] / CAPITAL) ** (1 / yrs) - 1
    ulcer = np.sqrt((ddser ** 2).mean())
    var95 = np.percentile(mr, 5)
    cvar95 = mr[mr <= var95].mean()
    win = mr[mr > 0]; loss = mr[mr < 0]
    print(f"  CAGR                : {100*cagr:+.1f}%          maxDD : {100*mdd:.1f}%")
    print(f"  Sharpe (disetahunkan): {ann:+.2f}          Sortino : {sortino:+.2f}")
    print(f"  Calmar              : {cagr/abs(mdd):.2f}           Ulcer : {100*ulcer:.1f}%")
    print(f"  VaR 95% bulanan     : {100*var95:+.1f}%      CVaR 95% : {100*cvar95:+.1f}%")
    print(f"  Bulan hijau         : {100*(mr>0).mean():.0f}%          "
          f"rata2 menang {100*win.mean():+.1f}%  rata2 kalah {100*loss.mean():+.1f}%")
    print(f"  Tail ratio (p95/|p5|): {np.percentile(mr,95)/abs(np.percentile(mr,5)):.2f}")

    print("\n  ANATOMI DRAWDOWN (5 terdalam):")
    da = dd_anatomy(eq).sort_values("dd").head(5)
    print(f"    {'mulai':<12}{'terdalam':<12}{'pulih':<12}{'dd':>8}{'durasi(hari)':>14}")
    for _, r in da.iterrows():
        pul = f"{r.pulih:%Y-%m-%d}" if r.pulih is not None else "BELUM"
        dur = (r.pulih - r.mulai).days if r.pulih is not None else (t.index[-1] - r.mulai).days
        print(f"    {r.mulai:%Y-%m-%d}  {r.terdalam:%Y-%m-%d}  {pul:<12}{100*r.dd:>7.1f}%{dur:>14}")

    print("\n  ROLLING 12 BULAN:")
    r12 = mr.rolling(12).sum().dropna()
    print(f"    jendela: {len(r12)}   negatif: {int((r12<0).sum())} ({100*(r12<0).mean():.0f}%)")
    print(f"    terburuk {100*r12.min():+.1f}%   median {100*r12.median():+.1f}%   "
          f"terbaik {100*r12.max():+.1f}%")

    # ---------------- C. TARGET USER ----------------
    print("\nBAGIAN C — APAKAH 'DD 10% + RETURN TIAP BULAN' MUNGKIN?")
    print("-" * 100)
    print("  Menskalakan eksposur (mengubah lot/modal) menggeser DD dan return SEBANDING.")
    print(f"  {'skala':>7}{'CAGR':>10}{'maxDD':>10}{'bln hijau':>12}{'bln merah beruntun':>20}")
    for k in (0.25, 0.5, 10 / (100 * abs(mdd)), 0.75, 1.0):
        s = t.pnl * k
        e2 = CAPITAL + s.cumsum()
        d2 = ((e2 - e2.cummax()) / e2.cummax()).min()
        m2 = s.resample("ME").sum(); m2 = m2[m2 != 0]
        c2 = (e2.iloc[-1] / CAPITAL) ** (1 / yrs) - 1
        st_ = mx = 0
        for v in m2:
            st_ = st_ + 1 if v < 0 else 0
            mx = max(mx, st_)
        tag = "  <- DD 10%" if abs(k - 10 / (100 * abs(mdd))) < 1e-9 else ""
        print(f"  {k:>7.2f}{100*c2:>9.1f}%{100*d2:>9.1f}%{100*(m2>0).mean():>11.0f}%"
              f"{mx:>20}{tag}")
    print("\n  Perhatikan: kolom 'bln hijau' TIDAK berubah saat skala diubah.")
    print("  Menurunkan eksposur mengecilkan DD DAN return secara proporsional, tapi TIDAK")
    print("  membuat bulan merah jadi hijau. Bulan merah adalah sifat sinyalnya, bukan ukurannya.")

    # ---------------- D. VONIS ----------------
    print("\nBAGIAN D — VONIS")
    print("=" * 100)
    k10 = 10 / (100 * abs(mdd))
    s = t.pnl * k10
    m2 = s.resample("ME").sum(); m2 = m2[m2 != 0]
    e2 = CAPITAL + s.cumsum()
    c2 = (e2.iloc[-1] / CAPITAL) ** (1 / yrs) - 1
    print(f"  DD 10% tercapai pada skala {k10:.2f}x -> CAGR {100*c2:+.1f}%/tahun.")
    print(f"  Tapi bulan hijau tetap {100*(m2>0).mean():.0f}% — {int((m2<0).sum())} dari "
          f"{len(m2)} bulan MERAH.")
    print(f"  Deflated Sharpe {p:.4f} " +
          ("(lolos)" if p >= 0.95 else "(TIDAK lolos ambang komite 0.95)"))
    print("\n  'Return tiap bulan' TIDAK bisa dicapai dengan menyetel strategi tunggal ini.")
    print("  Satu-satunya jalan yang sah adalah PORTOFOLIO dari beberapa edge yang")
    print("  berkorelasi rendah — bukan menyetel ulang satu sinyal Supertrend.")


if __name__ == "__main__":
    main()
