"""ETERNA fase-16: KOREKSI DUA KESALAHAN MODELKU.

User benar dua kali. Yang aku salah:

KESALAHAN 1 — lookback struktur.
  EA baris 259: `int bars = (int)ATR_Period;` -> lookback SL TERIKAT ke ATR_Period.
  Aku mengunci STRUCT=20 di fase 4-15 dan tetap 20 saat menyapu ATR {7,10,14,20}.
  Akibatnya SL 2-3x lebih lebar dari maksud EA. SL lebar itu BUATANKU.

KESALAHAN 2 — ukuran point.
  Harga XAU 3 desimal (4041.045) -> _Point = 0.001, jadi Manual_SL_Points=1000 = $1.00,
  BUKAN $10.00. Fase 1-3 (varian SL tetap) salah faktor 10x. Varian itu sudah ditinggalkan,
  tapi kesimpulannya jadi tidak sah dan tidak boleh dikutip lagi.

Akibat kesalahan 1: aku memilih H1 + lookback 20 = kotak dengan SL TERLEBAR dari semua
kombinasi (median 26,3% modal per trade @0.01 lot / $1000). User ingat DD ~12% dan SL
tidak lebar -> arahnya ke TF lebih rendah dengan lookback terikat ATR_Period.

Sapuan ini: struct_bars = atr_period (seperti EA), lintas M5/M15/M30/H1.
Metrik utama BUKAN net, melainkan maxDD% dan RISIKO MEDIAN PER TRADE — karena
survivability yang menentukan kelayakan, bukan return.

Jalankan: python research/eterna_corrected.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.01, 100.0, 0.50
CAPITAL, MIN_SL = 1000.0, 0.30
SIDEWAYS = (pd.Timestamp("2021-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC"))
BULL = (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2026-07-01", tz="UTC"))

TFS = {"M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h"}
PERIODS = [7, 10, 14, 20]          # dipakai untuk ATR *dan* lookback struktur (seperti EA)
MULT_E = [1.2, 1.8, 2.5]
MULT_T = [3.8, 5.0]
TPS = [3.0, 4.0]
MODES = ["conservative", "direct"]


def load_1m():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


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


def run(df, st_e, st_t, struct_bars, tp_r, mode):
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    slo = df["low"].rolling(struct_bars).min().shift(1).to_numpy()
    shi = df["high"].rolling(struct_bars).max().shift(1).to_numpy()
    pos = 0; entry = sl = tp = risk = 0.0; ei = 0; out = []
    for i in range(1, len(df)):
        if pos != 0:
            hit = None
            if pos == 1:
                hit = sl if l[i] <= sl else (tp if h[i] >= tp else None)
            else:
                hit = sl if h[i] >= sl else (tp if l[i] <= tp else None)
            if hit is not None:
                out.append((df.index[ei], pos, entry, hit, risk)); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos == -s:
            out.append((df.index[ei], pos, entry, o[i], risk)); pos = 0
        if pos != 0:
            continue
        if mode == "conservative" and (np.isnan(td[i]) or int(td[i]) != s):
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
    if not out:
        return None
    t = pd.DataFrame(out, columns=["t_in", "dir", "px_in", "px_out", "risk"])
    t["pnl"] = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT - COST
    t["R"] = t.pnl / (t.risk * LOT * CONTRACT)
    return t.set_index("t_in")


def ens(frames):
    parts = [f[["pnl", "risk"]].assign(pnl=f.pnl / len(frames)) for f in frames if f is not None]
    return pd.concat(parts).sort_index() if parts else None


def summarize(c, label, risk_series):
    if c is None or len(c) < 100:
        return None
    eq = CAPITAL + c.pnl.cumsum()
    dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
    w, l = c.loc[c.pnl > 0, "pnl"], c.loc[c.pnl <= 0, "pnl"]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() != 0 else np.inf
    yr = c.groupby(c.index.year).pnl.sum()
    m = c.pnl.resample("ME").sum(); m = m[m != 0]
    streak = mx = 0
    for v in m:
        streak = streak + 1 if v < 0 else 0
        mx = max(mx, streak)
    yrs = (c.index[-1] - c.index[0]).days / 365.25
    risk_pct = 100 * risk_series * LOT * CONTRACT / CAPITAL
    return {"konfigurasi": label, "n": len(c), "net": round(c.pnl.sum()),
            "PF": round(pf, 2), "maxDD%": round(dd, 1),
            "thn%": round(100 * (c.pnl.sum() / yrs) / CAPITAL, 1),
            "Ret/DD": round((100 * (c.pnl.sum() / yrs) / CAPITAL) / abs(dd), 2) if dd else np.nan,
            "hijau": f"{int((yr>0).sum())}/{len(yr)}", "merah%": round(100 * (m < 0).mean()),
            "beruntun": mx, "risk_med%": round(risk_pct.median(), 1),
            "risk_max%": round(risk_pct.max(), 1),
            "netSide": round(c.loc[SIDEWAYS[0]:SIDEWAYS[1]].pnl.sum())}


def main():
    df1m = load_1m()
    rows = []
    for tf_l, tf in TFS.items():
        d = df1m.resample(tf, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        print(f"\n=== {tf_l} ({len(d):,} bar) ===", flush=True)
        sts = {}
        for p in PERIODS:
            for m in set(MULT_E) | set(MULT_T):
                sts[(p, m)] = supertrend(d, p, m)
        for mode in MODES:
            for me in MULT_E:
                for tp in TPS:
                    # ENSEMBLE lintas periode {7,10,14,20} x tren {3.8,5.0}; struct = periode
                    frames = []
                    for p in PERIODS:
                        for mt in MULT_T:
                            frames.append(run(d, sts[(p, me)], sts[(p, mt)], p, tp, mode))
                    c = ens(frames)
                    if c is None:
                        continue
                    s = summarize(c, f"{tf_l} e{me} TP1:{tp:g} {mode[:4]}", c.risk)
                    if s:
                        rows.append(s)
        print(f"  terkumpul {len(rows)}", flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(r"C:\Quant\_MONITOR\eterna_corrected.csv", index=False)

    print("\n" + "=" * 132)
    print("SEMUA — diurut Ret/DD (return tahunan % dibagi maxDD %)")
    print("=" * 132)
    print(out.sort_values("Ret/DD", ascending=False).to_string(index=False))

    print("\n" + "=" * 132)
    print("YANG MEMENUHI SYARAT KELAYAKAN NYATA:")
    print("  maxDD <= 15%  DAN  risiko median <= 8% modal  DAN  untung di regime sideways")
    print("=" * 132)
    ok = out[(out["maxDD%"] >= -15) & (out["risk_med%"] <= 8) & (out.netSide > 0)]
    if len(ok):
        print(ok.sort_values("Ret/DD", ascending=False).to_string(index=False))
    else:
        print("  tidak ada")

    print("\nPembanding — konfigurasi LAMA yang salah (H1 struct 20):")
    print("  maxDD -28,3% | risiko median 26,3% | risiko max 41,5%")


if __name__ == "__main__":
    main()
