"""ETERNA fase-21: PARITAS ULANG — class live (versi tunggal ATR 16) vs kode riset.

Class lama (ensemble 32 + voting) sudah dibuang. Class baru memakai SATU set parameter,
jadi paritasnya harus diuji ulang dari nol. Tanpa ini, angka backtest tidak berlaku
untuk yang di-deploy.

Cara: jalankan class live bar demi bar pada potongan sejarah (seolah live), rekam
urutan aksinya, lalu bandingkan dengan posisi yang seharusnya dipegang menurut kode
riset (research/eterna_revalidate.py::run) pada bar yang sama.

Jalankan: python research/eterna_parity2.py
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
LOT, CONTRACT = 0.01, 100.0


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


def st_research(df, mult):
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
    return d


def research_states(h):
    """Posisi yang SEHARUSNYA dipegang di akhir tiap bar, menurut kode riset."""
    se, st = st_research(h, MULT_E), st_research(h, MULT_T)
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(P).min().shift(1).to_numpy()
    shi = h["high"].rolling(P).max().shift(1).to_numpy()
    n = len(h)
    out = np.zeros(n, dtype=np.int8)
    pos = 0; sl = tp = 0.0
    for i in range(1, n):
        if pos != 0:
            hit = None
            if pos == 1:
                hit = sl if lo[i] <= sl else (tp if hi[i] >= tp else None)
            else:
                hit = sl if hi[i] >= sl else (tp if lo[i] <= tp else None)
            if hit is not None:
                pos = 0
        if se[i] != se[i-1]:
            s = se[i]
            if pos == -s:
                pos = 0
            if pos == 0 and st[i] == s:
                raw = slo[i] if s == 1 else shi[i]
                if not np.isnan(raw):
                    dist = abs(h["close"].iloc[i] - raw)
                    if dist >= MIN_SL:
                        pos = s
                        sl = h["close"].iloc[i] - dist if s == 1 else h["close"].iloc[i] + dist
                        tp = (h["close"].iloc[i] + TP_R * dist if s == 1
                              else h["close"].iloc[i] - TP_R * dist)
        out[i] = pos
    return out


def main():
    h = load_h1()
    cfg = yaml.safe_load(open(r"C:\Quant\config.yaml", encoding="utf-8"))
    spec = [x for x in cfg["live"]["strategies"] if x["name"] == "eterna_xau"][0]
    states = research_states(h)
    print(f"H1 {len(h):,} bar | konfigurasi ATR {P}, entry x{MULT_E}, tren x{MULT_T}, TP 1:{TP_R:g}\n")

    # jalankan class live bar demi bar pada 600 bar terakhir (seolah live)
    N = 600
    start = len(h) - N
    mismatch = 0
    checked = 0
    first_bad = None

    class FeedTo:
        def __init__(self, upto):
            self.upto = upto
        def recent_bars(self, sym, n):
            # kembalikan bar M1 palsu: cukup pakai H1 karena class me-resample ke 1h
            return h.iloc[max(0, self.upto - 600):self.upto]

    strat = EternaStrategy(spec, cfg, FeedTo(start))
    strat._reconcile = lambda: None                # tanpa MT5; state internal yang diuji

    for i in range(start, len(h)):
        strat.data.upto = i + 1                    # bar ke-i sudah tertutup
        strat._last_bar_ts = None                  # paksa evaluasi ulang tiap bar
        r = strat.evaluate()
        live = 1 if r.action == "BUY" else (-1 if r.action == "SELL" else 0)
        want = int(states[i])
        checked += 1
        if live != want:
            mismatch += 1
            if first_bad is None:
                first_bad = (h.index[i], live, want)

    print(f"Bar diperiksa   : {checked}")
    print(f"Tidak cocok     : {mismatch}  ({100*mismatch/checked:.1f}%)")
    if first_bad:
        print(f"Beda pertama    : {first_bad[0]}  live={first_bad[1]} riset={first_bad[2]}")
    print()
    if mismatch == 0:
        print("PARITAS TERBUKTI — class live identik dengan kode riset.")
        print("Angka backtest sah dipakai untuk slot eterna_xau.")
    elif mismatch / checked < 0.02:
        print("Nyaris identik (<2% beda). Periksa sebabnya sebelum deploy —")
        print("biasanya beda perlakuan bar berjalan vs bar tertutup.")
    else:
        print("*** PARITAS GAGAL *** — JANGAN deploy sebelum sebabnya ketemu.")


if __name__ == "__main__":
    main()
