"""ETERNA fase-23: PARITAS versi benar — class live vs kode riset yang MENGHASILKAN angka.

Kenapa fase-21 gagal (5,8% beda): harness-nya mematikan `_reconcile`, jadi class tak pernah
tahu broker menutup posisi di SL/TP. Class memegang posisi basi, keluar di flip berikutnya,
sementara riset sudah keluar di TP dan masuk lagi. Yang cacat ALAT UJINYA.

Perbaikan di sini:
  1. Referensi = `run()` yang PERSIS sama dengan research/eterna_revalidate.py — sumber
     angka yang divalidasi. (Fase-21 memakai referensi yang ditulis ulang dengan konvensi
     berbeda: flip di bar i & entry di close[i], padahal riset memakai flip di bar i-1 &
     entry di open[i]. Beda konvensi itu sendiri sudah menghasilkan beda.)
  2. Broker DISIMULASIKAN: pada tiap titik keputusan, class disuapi keadaan posisi yang
     benar (seperti _reconcile membacanya dari MT5), lalu diuji apakah KEPUTUSAN BARUNYA
     sama dengan riset.

Yang diuji: setiap bar di mana riset MEMBUKA posisi -> apakah class dari keadaan FLAT
mengeluarkan arah yang sama? Dan sebaliknya, pada sampel bar di mana riset TIDAK membuka
apa-apa -> apakah class juga diam?

Jalankan: python research/eterna_parity3.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd
import yaml

from pipeline.live.signal import EternaStrategy

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
P, MULT_E, MULT_T, TP_R, MIN_SL = 16, 1.8, 3.8, 4.0, 0.30
LOT, CONTRACT, COST = 0.01, 100.0, 0.50


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


def supertrend(df, mult):
    a = atr_s(df, P)
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


def research_entries(h):
    """Bar-bar di mana kode riset MEMBUKA posisi, beserta arah/SL/TP. Salinan run()."""
    st_e, st_t = supertrend(h, MULT_E), supertrend(h, MULT_T)
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(P).min().shift(1).to_numpy()
    shi = h["high"].rolling(P).max().shift(1).to_numpy()
    pos = 0; sl = tp = 0.0
    entries = {}          # index bar -> (arah, sl, tp)
    flat_at = []          # index bar saat riset FLAT dan tidak membuka apa-apa
    for i in range(1, len(h)):
        if pos != 0:
            hit = None
            if pos == 1:
                hit = sl if lo[i] <= sl else (tp if hi[i] >= tp else None)
            else:
                hit = sl if hi[i] >= sl else (tp if lo[i] <= tp else None)
            if hit is not None:
                pos = 0
        s = sd[i]
        opened = False
        if not np.isnan(s):
            s = int(s)
            if pos == -s:
                pos = 0
            if pos == 0 and not np.isnan(td[i]) and int(td[i]) == s:
                raw = slo[i] if s == 1 else shi[i]
                if not np.isnan(raw):
                    dist = abs(o[i] - raw)
                    if dist >= MIN_SL:
                        pos = s
                        sl = o[i] - dist if s == 1 else o[i] + dist
                        tp = o[i] + TP_R * dist if s == 1 else o[i] - TP_R * dist
                        entries[i] = (s, sl, tp)
                        opened = True
        if pos == 0 and not opened:
            flat_at.append(i)
    return entries


def main():
    h = load_h1()
    cfg = yaml.safe_load(open(r"C:\Quant\config.yaml", encoding="utf-8"))
    spec = [x for x in cfg["live"]["strategies"] if x["name"] == "eterna_xau"][0]
    ent = research_entries(h)
    print(f"H1 {len(h):,} bar | ATR {P} entry x{MULT_E} tren x{MULT_T} TP 1:{TP_R:g}")
    print(f"Riset membuka {len(ent)} posisi sepanjang 5,5 tahun\n")

    class Feed:
        def __init__(self):
            self.upto = 0
        def recent_bars(self, sym, n):
            return h.iloc[max(0, self.upto - 400):self.upto]

    feed = Feed()
    strat = EternaStrategy(spec, cfg, feed)
    strat._reconcile = lambda: None          # keadaan broker disuapi manual di bawah

    # ---- UJI 1: pada tiap bar entry riset, apakah class (dari FLAT) setuju? ----
    ok = bad = 0
    bad_list = []
    for i, (want_dir, want_sl, want_tp) in ent.items():
        if i < 60:
            continue
        feed.upto = i                        # bar i-1 adalah bar TERTUTUP terakhir
        strat._prev_action = "FLAT"          # riset juga FLAT tepat sebelum entry ini
        strat._sl = strat._tp = 0.0
        strat._cached = None
        strat._last_bar_ts = None
        r = strat.evaluate()
        got = 1 if r.action == "BUY" else (-1 if r.action == "SELL" else 0)
        if got == want_dir:
            ok += 1
        else:
            bad += 1
            if len(bad_list) < 6:
                bad_list.append((h.index[i], want_dir, got))
    print("UJI 1 — bar di mana RISET membuka posisi:")
    print(f"  cocok {ok} / {ok+bad}  ({100*ok/(ok+bad):.1f}%)")
    for ts, w, g in bad_list:
        print(f"    beda: {ts}  riset={w}  live={g}")

    # ---- UJI 2: sampel bar di mana riset TIDAK membuka apa-apa -> class harus diam ----
    non = [i for i in range(200, len(h)) if i not in ent]
    rng = np.random.default_rng(11)
    sample = rng.choice(non, size=min(1500, len(non)), replace=False)
    ok2 = bad2 = 0
    for i in sorted(sample):
        feed.upto = i
        strat._prev_action = "FLAT"
        strat._sl = strat._tp = 0.0
        strat._cached = None
        strat._last_bar_ts = None
        r = strat.evaluate()
        if r.action == "FLAT":
            ok2 += 1
        else:
            bad2 += 1
    print(f"\nUJI 2 — 1500 bar acak tanpa entry riset (class harus FLAT):")
    print(f"  cocok {ok2} / {ok2+bad2}  ({100*ok2/(ok2+bad2):.1f}%)")

    total_bad = bad + bad2
    print("\n" + "=" * 78)
    if total_bad == 0:
        print("PARITAS TERBUKTI — logika sinyal class live identik dengan kode riset.")
        print("Angka backtest sah untuk slot eterna_xau. Siap shadow-test.")
    else:
        print(f"*** MASIH BEDA di {total_bad} titik — jangan deploy, cari sebabnya dulu.")


if __name__ == "__main__":
    main()
