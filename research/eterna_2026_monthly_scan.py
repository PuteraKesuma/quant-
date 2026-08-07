"""ETERNA fase-22: apakah ADA konfigurasi yang seluruh bulan 2026-nya hijau?

User ingat "setiap bulan 2026 profit". Skrip ini memeriksa seluruh plateau ATR (10..24)
untuk 2026 (Jan s/d hari ini, data lokal + Dukascopy segar), dan menampilkan PnL bulanan
apa adanya.

PERINGATAN METODOLOGIS yang harus dibaca bersama hasilnya:
  Memilih konfigurasi KARENA seluruh bulannya hijau di SATU tahun adalah pemilihan
  berdasarkan hasil (outcome fitting) — persis cara Golden dan mr_xau lolos dulu sebelum
  terbukti artifact. 8 bulan itu sampel sangat kecil; peluang satu konfigurasi kebetulan
  hijau 8/8 tidaklah mustahil ketika kita memeriksa banyak konfigurasi.
  Karena itu skrip ini JUGA menampilkan angka 5,5 tahun tiap konfigurasi, supaya terlihat
  apakah yang "8/8 hijau di 2026" itu juga sehat sepanjang sejarah, atau cuma beruntung.

Jalankan: python research/eterna_2026_monthly_scan.py
"""
import warnings
warnings.filterwarnings("ignore")

import datetime as dt
import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.01, 100.0, 0.50
CAPITAL, MIN_SL = 1000.0, 0.30
MULT_E, MULT_T, TP_R = 1.8, 3.8, 4.0
START26 = pd.Timestamp("2026-01-01", tz="UTC")


def load_fresh():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.set_index("ts")
    import dukascopy_python
    from dukascopy_python.instruments import INSTRUMENT_FX_METALS_XAU_USD
    last = df.index[-1]
    add = dukascopy_python.fetch(
        INSTRUMENT_FX_METALS_XAU_USD, dukascopy_python.INTERVAL_MIN_1,
        dukascopy_python.OFFER_SIDE_BID,
        (last - pd.Timedelta(days=2)).to_pydatetime().replace(tzinfo=None),
        dt.datetime.utcnow())
    add.index = pd.to_datetime(add.index, utc=True)
    add = add[["open", "high", "low", "close"]]
    add = add[add.index > last]
    m = pd.concat([df, add]).sort_index()
    m = m[~m.index.duplicated(keep="first")]
    print(f"  data s/d {m.index[-1]}  (+{len(add):,} bar segar)")
    return m.resample("1h", label="left", closed="left").agg(
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


def run(h, p, tp_r=TP_R):
    st_e, st_t = supertrend(h, p, MULT_E), supertrend(h, p, MULT_T)
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(p).min().shift(1).to_numpy()
    shi = h["high"].rolling(p).max().shift(1).to_numpy()
    pos = 0; entry = sl = tp = risk = 0.0; ei = 0; out = []
    for i in range(1, len(h)):
        if pos != 0:
            hit = None
            if pos == 1:
                hit = sl if lo[i] <= sl else (tp if hi[i] >= tp else None)
            else:
                hit = sl if hi[i] >= sl else (tp if lo[i] <= tp else None)
            if hit is not None:
                out.append((h.index[ei], pos, entry, hit, risk)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((h.index[ei], pos, entry, o[i], risk)); pos = 0
        if pos != 0 or np.isnan(td[i]) or int(td[i]) != s:
            continue
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(o[i] - raw)
        if dist < MIN_SL:
            continue
        pos, entry, ei, risk = s, o[i], i, dist
        sl = o[i] - dist if s == 1 else o[i] + dist
        tp = o[i] + tp_r * dist if s == 1 else o[i] - tp_r * dist
    t = pd.DataFrame(out, columns=["t_in", "dir", "px_in", "px_out", "risk"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    return t.set_index("t_in")


def main():
    print("Memuat data ...")
    h = load_fresh()
    print()

    months = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu"]
    print("=" * 122)
    print("PnL BULANAN 2026 (modal $1000, lot 0.01) — seluruh plateau ATR")
    print("=" * 122)
    print(f"{'ATR':>4} " + "".join(f"{m:>9}" for m in months)
          + f"{'2026':>10}{'hijau':>7} | {'5,5thn net':>11}{'maxDD%':>8}{'Ret/DD':>8}{'thn hijau':>10}")
    print("-" * 122)
    best = []
    for p in (10, 12, 14, 16, 18, 20, 24):
        t = run(h, p)
        t26 = t[t.index >= START26]
        m = t26.pnl.resample("ME").sum()
        vals = list(m.values)
        green = int((m > 0).sum())
        # metrik 5,5 tahun
        eq = CAPITAL + t.pnl.cumsum()
        dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
        yrs = (t.index[-1] - t.index[0]).days / 365.25
        thn = 100 * (t.pnl.sum() / yrs) / CAPITAL
        yr = t.pnl.groupby(t.index.year).sum()
        row = f"{p:>4} " + "".join(f"{v:>9.0f}" for v in vals[:8])
        row += f"{t26.pnl.sum():>10.0f}{green:>4}/{len(m):<2}"
        row += f" | {t.pnl.sum():>11.0f}{dd:>8.1f}{thn/abs(dd) if dd else 0:>8.2f}"
        row += f"{int((yr>0).sum()):>6}/{len(yr):<3}"
        print(row)
        best.append((p, green, len(m), t26.pnl.sum(), dd, thn / abs(dd) if dd else 0))

    print("-" * 122)
    allg = [b for b in best if b[1] == b[2]]
    print()
    if allg:
        print(f"Konfigurasi dengan SELURUH bulan 2026 hijau: {[b[0] for b in allg]}")
    else:
        print("TIDAK ADA konfigurasi di plateau yang seluruh bulan 2026-nya hijau.")
        mx = max(best, key=lambda b: b[1])
        print(f"Terbanyak: ATR {mx[0]} dengan {mx[1]}/{mx[2]} bulan hijau.")
    print()
    print("CATATAN PENTING: memilih konfigurasi KARENA 8/8 bulannya hijau adalah memilih")
    print("berdasarkan hasil. 8 bulan = sampel sangat kecil. Kolom kanan (5,5 tahun) yang")
    print("menentukan apakah sebuah konfigurasi layak — bukan kolom 2026.")


if __name__ == "__main__":
    main()
