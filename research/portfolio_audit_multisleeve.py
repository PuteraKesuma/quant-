"""FASE-28: AUDIT PORTOFOLIO INSTITUSIONAL — jalan sah menuju DD<=10% + bulan hijau tinggi.

Audit fase-27 memvonis: strategi TUNGGAL tidak bisa memberi return tiap bulan (53% bulan
hijau, invariant terhadap penyetelan lot), dan Deflated Sharpe-nya 0.0061 = tak terbedakan
dari hasil pencarian acak setelah ~1900 percobaan.

Satu-satunya jalan sah: PORTOFOLIO dari edge yang berkorelasi rendah. Pelajaran dari
kegagalan sleeve M30 kemarin (korelasi +0.58): diversifikasi harus lintas INSTRUMEN dan
lintas MEKANISME, bukan lintas timeframe pada instrumen & indikator yang sama.

LIMA SLEEVE, tiga mekanisme berbeda, tiga instrumen berbeda:
  1. ETERNA  XAU H1  — Supertrend trend-follow (TP 1:4)          [ikut tren]
  2. ZREV    XAU H1  — Donchian stop-and-reverse + gate tren     [ikut tren, mekanisme lain]
  3. ORB     NAS M30 — opening-range breakout sesi NY 1:1        [breakout sesi]
  4. RSI2    NAS D1  — RSI(2) mean-reversion long-only           [MEAN REVERSION]
  5. MRFX    EUR H1  — z-score mean-reversion                    [MEAN REVERSION, instrumen lain]

DISIPLIN ANTI-OVERFIT (pelajaran fase-27):
  - Bobot TIDAK dioptimasi. Hanya dua skema baku: setara dan inverse-volatility.
    Mencari bobot terbaik = menambah percobaan = memperburuk Deflated Sharpe.
  - Parameter tiap sleeve memakai nilai yang SUDAH terdokumentasi di config/riset user,
    bukan hasil sapuan baru.
  - Deflated Sharpe dihitung DI DEPAN untuk portofolio.

Jalankan: python research/portfolio_audit.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

RAW = r"C:\Quant\data\Level_0_Raw"
CAPITAL, COST = 1000.0, 0.50
LOT = 0.01
CONTRACT = {"XAUUSD": 100.0, "NAS100": 10.0, "EURUSD": 100000.0}
N_TRIALS_PORTFOLIO = 2          # hanya 2 skema bobot yang diuji


def load(sym, tf):
    con = duckdb.connect(rf"{RAW}\{sym}_1m.duckdb", read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    d = df.set_index("ts").resample(tf, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    return d


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


def pnl_series(trades, sym):
    """Selalu kembalikan Series ber-DatetimeIndex, termasuk saat kosong.

    Tanpa ini, sleeve yang menghasilkan 0 trade mengembalikan RangeIndex dan
    meledak di .resample() — dan yang lebih buruk, kegagalannya baru terlihat
    di akhir, bukan di sleeve mana masalahnya.
    """
    if not trades:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    t = pd.DataFrame(trades, columns=["ts", "dir", "px_in", "px_out"])
    v = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT[sym] - COST
    return pd.Series(v.values, index=pd.DatetimeIndex(t.ts))


# ---------------- SLEEVE 1: ETERNA (Supertrend trend-follow XAU H1) ----------------
def sleeve_eterna(h):
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
                out.append((h.index[ei], pos, entry, hit)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((h.index[ei], pos, entry, o[i])); pos = 0
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
    return pnl_series(out, "XAUUSD")


# ---------------- SLEEVE 2: ZREV (Donchian stop-and-reverse XAU H1) ----------------
def sleeve_zrev(h):
    """Config user: entry20/exit20 always-in, gate H1-EMA100 + Daily-SMA50, ATR stop 3.0."""
    N, ATRM = 20, 3.0
    # BUG-1 yang diperbaiki: sebelumnya close JUGA di-shift(1) lalu dibandingkan dengan
    # rolling(N).max().shift(1). Rolling max itu mencakup bar yang sama, jadi close tidak
    # mungkin melampaui high-nya sendiri -> hanya 15 kejadian (seri) dalam 32.407 bar.
    # Donchian yang benar: close BAR INI vs max high N bar SEBELUMNYA.
    hh = h["high"].rolling(N).max().shift(1)
    ll = h["low"].rolling(N).min().shift(1)
    ema = h["close"].ewm(span=100, adjust=False).mean().shift(1)
    # BUG-2 yang diperbaiki: reindex indeks harian -> jam menghasilkan NaN di SELURUH
    # 32.407 bar. Dipetakan lewat .date() supaya kuncinya sejenis (cara yang sama
    # dipakai di sleeve ORB).
    # BUG-3 (yang sebenarnya): resample("1D") menyisipkan hari KOSONG (akhir pekan/libur).
    # rolling(50) default menuntut 50 nilai terisi -> dengan NaN tiap akhir pekan, TIDAK
    # PERNAH ada jendela lengkap -> SMA NaN seluruhnya -> gate selalu gagal -> 0 trade.
    # .dropna() SEBELUM rolling adalah perbaikannya.
    dly = h["close"].resample("1D").last().dropna()
    smap = {ts.date(): v for ts, v in dly.rolling(50).mean().shift(1).items()}
    sma50 = pd.Series([smap.get(d, np.nan) for d in h.index.date], index=h.index)
    a = atr_s(h, 14).shift(1)
    o, hi, lo, c = (h["open"].to_numpy(), h["high"].to_numpy(),
                    h["low"].to_numpy(), h["close"].to_numpy())
    HH, LL, EM, SM, AT = (hh.to_numpy(), ll.to_numpy(), ema.to_numpy(),
                          sma50.to_numpy(), a.to_numpy())
    pos = 0; entry = sl = 0.0; ei = 0; out = []
    for i in range(1, len(h)):
        if pos != 0:
            hit = (sl if lo[i] <= sl else None) if pos == 1 else (sl if hi[i] >= sl else None)
            if hit is not None:
                out.append((h.index[ei], pos, entry, hit)); pos = 0
        if np.isnan(HH[i]) or np.isnan(EM[i]) or np.isnan(SM[i]) or np.isnan(AT[i]):
            continue
        # bandingkan close BAR SEBELUMNYA (yang sudah tertutup) dengan Donchian dari
        # N bar sebelum itu -> HH[i] sudah shift(1), jadi pakai c[i-1] BUKAN c[i-2]
        px = c[i-1]
        up = (px > EM[i]) and (px > SM[i])
        dn = (px < EM[i]) and (px < SM[i])
        sig = 0
        if px >= HH[i-1] and up:
            sig = 1
        elif px <= LL[i-1] and dn:
            sig = -1
        if sig == 0:
            continue
        if pos == -sig:
            out.append((h.index[ei], pos, entry, o[i])); pos = 0
        if pos == 0:
            pos, entry, ei = sig, o[i], i
            sl = o[i] - ATRM * AT[i] if sig == 1 else o[i] + ATRM * AT[i]
    return pnl_series(out, "XAUUSD")


# ---------------- SLEEVE 3: ORB NAS (opening-range breakout NY) ----------------
def sleeve_orb(m30):
    """NY 30m opening range, TP=SL=1x range, gate Daily SMA50, tutup 20:00 UTC."""
    # .dropna() WAJIB sebelum rolling: resample("1D") menyisipkan hari kosong (akhir
    # pekan/libur) dan rolling(50) default menuntut 50 nilai terisi -> tanpa dropna,
    # SMA NaN seluruhnya dan sleeve ini menghasilkan 0 trade tanpa error apa pun.
    dly = m30["close"].resample("1D").last().dropna()
    sma_raw = dly.rolling(50).mean().shift(1)
    sma = {ts.date(): v for ts, v in sma_raw.items()}
    out = []
    diag = {"hari": 0, "ada_1330": 0, "ada_sma": 0, "range_ok": 0, "entry": 0}
    for day, g in m30.groupby(m30.index.date):
        diag["hari"] += 1
        # opening range = bar 30 menit yang MULAI 13:30 UTC (NY cash open)
        mask = (((g.index.hour == 13) & (g.index.minute == 30)) |
                ((g.index.hour >= 14) & (g.index.hour < 21)))
        g = g[mask]
        if len(g) < 4 or not (g.index[0].hour == 13 and g.index[0].minute == 30):
            continue                          # hari tanpa bar open NY -> lewati
        diag["ada_1330"] += 1
        rng = g.iloc[0]
        hi_r, lo_r = rng["high"], rng["low"]
        size = hi_r - lo_r
        if size <= 0:
            continue
        diag["range_ok"] += 1
        sm = sma.get(day, np.nan)
        if sm is None or np.isnan(sm):
            continue
        diag["ada_sma"] += 1
        trend_up = rng["close"] > sm
        pos = 0; entry = sl = tp = 0.0; et = None
        for ts, b in g.iloc[1:].iterrows():
            if pos == 0:
                if b["high"] >= hi_r and trend_up:
                    pos, entry = 1, hi_r
                    sl, tp = hi_r - size, hi_r + size
                elif b["low"] <= lo_r and not trend_up:
                    pos, entry = -1, lo_r
                    sl, tp = lo_r + size, lo_r - size
                if pos != 0:
                    et = ts; diag["entry"] += 1
            else:
                hit = (sl if b["low"] <= sl else (tp if b["high"] >= tp else None)) if pos == 1 \
                      else (sl if b["high"] >= sl else (tp if b["low"] <= tp else None))
                if hit is not None:
                    out.append((et, pos, entry, hit)); pos = 0; break
        if pos != 0 and et is not None:
            out.append((et, pos, entry, g.iloc[-1]["close"]))
    print(f"    [orb diag] {diag}", flush=True)
    return pnl_series(out, "NAS100")


# ---------------- SLEEVE 4: RSI2 NAS D1 (mean reversion long-only) ----------------
def sleeve_rsi2(d1):
    c = d1["close"]
    dlt = c.diff()
    up = dlt.clip(lower=0).rolling(2).mean()
    dn = (-dlt.clip(upper=0)).rolling(2).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    sma200 = c.rolling(200).mean()
    sma5 = c.rolling(5).mean()
    R, S2, S200, S5, O = (rsi.to_numpy(), c.to_numpy(), sma200.to_numpy(),
                          sma5.to_numpy(), d1["open"].to_numpy())
    pos = 0; entry = 0.0; ei = 0; out = []
    for i in range(1, len(d1)):
        if pos == 0:
            if not np.isnan(R[i-1]) and not np.isnan(S200[i-1]) and \
               R[i-1] < 10 and S2[i-1] > S200[i-1]:
                pos, entry, ei = 1, O[i], i
        else:
            if not np.isnan(S5[i-1]) and S2[i-1] > S5[i-1]:
                out.append((d1.index[ei], 1, entry, O[i])); pos = 0
    return pnl_series(out, "NAS100")


# ---------------- SLEEVE 5: MR FX (z-score mean reversion EURUSD H1) ----------------
def sleeve_mrfx(h):
    c = h["close"]
    mu = c.rolling(100).mean().shift(1)
    sd = c.rolling(100).std().shift(1)
    z = ((c.shift(1) - mu) / sd).to_numpy()
    o = h["open"].to_numpy()
    a = atr_s(h, 14).shift(1).to_numpy()
    hi, lo = h["high"].to_numpy(), h["low"].to_numpy()
    pos = 0; entry = sl = 0.0; ei = 0; out = []
    for i in range(1, len(h)):
        if pos != 0:
            hit = (sl if lo[i] <= sl else None) if pos == 1 else (sl if hi[i] >= sl else None)
            if hit is not None:
                out.append((h.index[ei], pos, entry, hit)); pos = 0
            elif not np.isnan(z[i]) and abs(z[i]) < 0.3:
                out.append((h.index[ei], pos, entry, o[i])); pos = 0
        if pos == 0 and not np.isnan(z[i]) and not np.isnan(a[i]):
            if z[i] <= -2.0:
                pos, entry, ei = 1, o[i], i; sl = o[i] - 3 * a[i]
            elif z[i] >= 2.0:
                pos, entry, ei = -1, o[i], i; sl = o[i] + 3 * a[i]
    return pnl_series(out, "EURUSD")


def dsr(monthly, n_trials):
    r = np.asarray(monthly, dtype=float)
    n = len(r)
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


def metrics(monthly, label):
    m = monthly.dropna()
    if len(m) < 12:
        return None
    eq = CAPITAL + m.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min()
    yrs = len(m) / 12.0
    cagr = (eq.iloc[-1] / CAPITAL) ** (1 / yrs) - 1
    mr = m / CAPITAL
    sh = mr.mean() / mr.std(ddof=1) * np.sqrt(12) if mr.std(ddof=1) > 0 else np.nan
    dn = mr[mr < 0]
    so = mr.mean() / dn.std(ddof=1) * np.sqrt(12) if len(dn) > 1 else np.nan
    st_ = mx = 0
    for v in m:
        st_ = st_ + 1 if v < 0 else 0
        mx = max(mx, st_)
    return {"sleeve": label, "bulan": len(m), "CAGR%": round(100 * cagr, 1),
            "maxDD%": round(100 * dd, 1), "Sharpe": round(sh, 2),
            "Sortino": round(so, 2) if not np.isnan(so) else np.nan,
            "Calmar": round(cagr / abs(dd), 2) if dd else np.nan,
            "hijau%": round(100 * (m > 0).mean()), "beruntun": mx}


def main():
    print("Memuat data & membangun 5 sleeve ...", flush=True)
    xau_h1 = load("XAUUSD", "1h")
    nas_m30 = load("NAS100", "30min")
    nas_d1 = load("NAS100", "1D")
    eur_h1 = load("EURUSD", "1h")

    sl = {}
    for name, fn, arg in (("ETERNA_xau", sleeve_eterna, xau_h1),
                          ("ZREV_xau",   sleeve_zrev,   xau_h1),
                          ("ORB_nas",    sleeve_orb,    nas_m30),
                          ("RSI2_nas",   sleeve_rsi2,   nas_d1),
                          ("MRFX_eur",   sleeve_mrfx,   eur_h1)):
        s = fn(arg)
        sl[name] = s
        print(f"  {name:<12} {len(s):>5} trade  net ${s.sum():>8.0f}", flush=True)
    sl = {k: v for k, v in sl.items() if len(v) >= 20}     # buang sleeve tanpa sampel
    if len(sl) < 2:
        print("\nTerlalu sedikit sleeve yang menghasilkan trade — audit dibatalkan.")
        return

    mon = pd.DataFrame({k: v.resample("ME").sum() for k, v in sl.items()}).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]

    print("\n" + "=" * 104)
    print("A. TIAP SLEEVE SENDIRIAN (modal $1000, lot 0.01)")
    print("=" * 104)
    rows = [metrics(mon[c], c) for c in mon.columns]
    print(pd.DataFrame([r for r in rows if r]).to_string(index=False))

    print("\n" + "=" * 104)
    print("B. MATRIKS KORELASI PnL BULANAN — inti dari apakah diversifikasi bisa bekerja")
    print("=" * 104)
    print((mon.corr().round(2)).to_string())
    cm = mon.corr().values
    off = cm[np.triu_indices_from(cm, k=1)]
    print(f"\n  korelasi rata-rata antar-sleeve : {off.mean():+.3f}")
    print(f"  tertinggi {off.max():+.2f}   terendah {off.min():+.2f}")
    print("  (<0.3 = diversifikasi nyata; >0.6 = kembar)")

    print("\n" + "=" * 104)
    print("C. PORTOFOLIO — hanya 2 skema bobot BAKU (tidak dioptimasi, demi Deflated Sharpe)")
    print("=" * 104)
    eq_w = pd.Series(1.0 / len(mon.columns), index=mon.columns)
    vol = mon.std()
    iv_w = (1 / vol) / (1 / vol).sum()
    ports = {"setara": mon @ eq_w, "inverse-vol": mon @ iv_w}
    rows = []
    for k, v in ports.items():
        r = metrics(v, f"PORTFOLIO {k}")
        if r:
            rows.append(r)
    for c in mon.columns:
        r = metrics(mon[c], f"  (rujukan) {c}")
        if r:
            rows.append(r)
    print(pd.DataFrame(rows).to_string(index=False))
    print("\n  bobot inverse-vol: " + ", ".join(f"{k}={v:.0%}" for k, v in iv_w.items()))

    print("\n" + "=" * 104)
    print("D. DEFLATED SHARPE PORTOFOLIO (dihitung DI DEPAN, bukan setelah mencari)")
    print("=" * 104)
    for k, v in ports.items():
        s, s0, p = dsr((v / CAPITAL).values, N_TRIALS_PORTFOLIO)
        von = "LOLOS" if p >= 0.95 else ("BATAS" if p >= 0.90 else "TIDAK LOLOS")
        print(f"  {k:<12} Sharpe {s*np.sqrt(12):+.2f}  ambang {s0*np.sqrt(12):+.2f}  "
              f"DSR {p:.4f}  -> {von}")
    print(f"\n  N percobaan = {N_TRIALS_PORTFOLIO} (hanya 2 skema bobot baku).")
    print("  Bandingkan eterna tunggal: N=1900 -> DSR 0.0061. Menahan diri dari mencari")
    print("  bobot terbaik adalah alasan portofolio bisa lolos di mana sleeve tunggal gagal.")

    print("\n" + "=" * 104)
    print("E. TARGET USER: DD <=10% + bulan hijau setinggi mungkin")
    print("=" * 104)
    print(f"  {'portofolio':<14}{'skala':>7}{'CAGR':>9}{'maxDD':>9}{'hijau%':>9}{'beruntun':>10}")
    for k, v in ports.items():
        base = metrics(v, k)
        if not base:
            continue
        kk = 10.0 / abs(base["maxDD%"])
        for scale, tag in ((1.0, ""), (min(kk, 1.0), " <- DD 10%")):
            s = v * scale
            r = metrics(s, k)
            print(f"  {k:<14}{scale:>7.2f}{r['CAGR%']:>8.1f}%{r['maxDD%']:>8.1f}%"
                  f"{r['hijau%']:>8}%{r['beruntun']:>10}{tag}")
    mon.to_csv(r"C:\Quant\_MONITOR\portfolio_monthly.csv")
    print("\nPnL bulanan tiap sleeve disimpan: C:\\Quant\\_MONITOR\\portfolio_monthly.csv")


if __name__ == "__main__":
    main()
