"""ETERNA fase-7: BEST PRACTICE — sizing berbasis risiko, filter SL lebar, ensemble.

Fase 1-6 sudah menyapu 458 konfigurasi. Menyapu lagi = overfit, bukan best practice.
Fase ini memperbaiki hal-hal STRUKTURAL yang belum pernah disentuh:

1. SIZING BERBASIS RISIKO (bukan lot tetap).
   Temuan fase-4: avgR positif tapi dolar negatif -> trade ber-SL LEBAR yang menghabisi.
   Dengan lot tetap, trade SL $20 mempertaruhkan 4x trade SL $5. Itu cacat struktural.
   Perbaikan: lot = (equity * risk_pct) / (jarak_SL * contract), dengan compounding.

2. FILTER SL LEBAR (dari _risk_ok() milik sistem user sendiri, aturan WMT).
   Lewati trade yang risikonya melebihi batas walau sudah di lot minimum 0.01.
   Ini menjawab langsung patologi di poin 1.

3. ENSEMBLE, bukan pilih-satu-pemenang.
   Mengambil "terbaik dari 458" = overfit. Keranjang setara dari konfigurasi bertetangga
   di plateau jauh lebih tahan di luar sampel.

Kendala nyata: di modal $1000 dengan lot minimum 0.01 XAU, satu trade ber-SL $8 sudah
= $8 risiko = 0.8% equity. Jadi sizing ke bawah TIDAK bisa; yang bisa cuma MENOLAK trade.
Skrip ini mengukur seberapa jauh itu menolong.

Jalankan: python research/eterna_bestpractice.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
CONTRACT = 100.0
MIN_LOT, LOT_STEP = 0.01, 0.01
CAPITAL0 = 1000.0
COST_PER_001 = 0.25          # biaya per 0.01 lot
MIN_SL_DIST = 0.50
SESSIONS = {"asia": (23, 30, 4, 0), "ny": (11, 30, 16, 0)}

# plateau M30 sesi NY (fase-4): 4 tetangga -> dipakai sebagai ENSEMBLE
BASKET = [("30min", 1.2, 20, 3.0, "ny"), ("30min", 1.2, 10, 3.0, "ny"),
          ("30min", 1.2, 20, 4.0, "ny"), ("30min", 1.2, 10, 4.0, "ny")]
RISK_CAPS = [0.008, 0.012, 0.020, 0.030, None]   # batas risiko per trade (fraksi equity)


def load_1m():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def atr(df, n):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def supertrend(df, period, mult):
    a = atr(df, period)
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


def in_session(idx, sess):
    sh, sm, eh, em = sess
    mins = idx.hour * 60 + idx.minute
    s, e = sh * 60 + sm, eh * 60 + em
    return (mins >= s) & (mins < e) if s < e else (mins >= s) | (mins < e)


def signals(df, st_e, sb, tp_r, sess):
    """Hasilkan daftar sinyal MENTAH (belum di-size): entry, sl, tp, arah, waktu."""
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    ok = in_session(df.index, sess)
    o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
    slo = df["low"].rolling(sb).min().shift(1).to_numpy()
    shi = df["high"].rolling(sb).max().shift(1).to_numpy()
    pos, entry, sl, tp, ei, out = 0, 0.0, 0.0, 0.0, 0, []
    for i in range(1, len(df)):
        if pos != 0:
            hit = None
            if pos == 1:
                hit = sl if l[i] <= sl else (tp if tp and h[i] >= tp else None)
            else:
                hit = sl if h[i] >= sl else (tp if tp and l[i] <= tp else None)
            if hit is not None:
                out.append((df.index[ei], pos, entry, hit, abs(entry - sl))); pos = 0
        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)
        if pos != 0 and pos != s:
            out.append((df.index[ei], pos, entry, o[i], abs(entry - sl))); pos = 0
        if pos != 0 or not ok[i]:
            continue
        px = o[i]
        raw = slo[i] if s == 1 else shi[i]
        if np.isnan(raw):
            continue
        dist = abs(px - raw)
        if dist < MIN_SL_DIST:
            continue
        pos, entry, ei = s, px, i
        sl = px - dist if s == 1 else px + dist
        tp = (px + tp_r * dist if s == 1 else px - tp_r * dist) if tp_r else 0.0
    return pd.DataFrame(out, columns=["t_in", "dir", "px_in", "px_out", "risk_dist"])


def simulate(sig, risk_cap, risk_target=0.01):
    """Jalankan berurutan dengan equity COMPOUNDING + filter risiko.

    lot ideal = equity*risk_target / (risk_dist*CONTRACT), dibulatkan ke step 0.01,
    minimal MIN_LOT. Kalau risiko pada MIN_LOT masih > risk_cap*equity -> trade DILEWATI.
    """
    eq = CAPITAL0
    rows = []
    for r in sig.itertuples():
        risk_at_min = r.risk_dist * CONTRACT * MIN_LOT
        if risk_cap is not None and risk_at_min > risk_cap * eq:
            continue                                   # SL terlalu lebar -> lewati
        ideal = (eq * risk_target) / (r.risk_dist * CONTRACT)
        lot = max(MIN_LOT, np.floor(ideal / LOT_STEP) * LOT_STEP)
        pnl = (r.px_out - r.px_in) * r.dir * lot * CONTRACT - COST_PER_001 * (lot / MIN_LOT)
        eq += pnl
        rows.append((r.t_in, pnl, eq, lot))
        if eq <= 100:
            break                                      # akun praktis habis
    if not rows:
        return None
    return pd.DataFrame(rows, columns=["t_in", "pnl", "eq", "lot"])


def report(t, label):
    if t is None or len(t) < 50:
        return None
    eq = t["eq"]          # NB: t.eq = method DataFrame.eq(), bukan kolom
    peak = eq.cummax()
    dd_pct = ((eq - peak) / peak).min() * 100
    years = (t.t_in.iloc[-1] - t.t_in.iloc[0]).days / 365.25
    cagr = ((eq.iloc[-1] / CAPITAL0) ** (1 / years) - 1) * 100 if years > 0 else np.nan
    m = t.set_index("t_in").pnl.resample("ME").sum()
    m = m[m != 0]
    red = 100 * (m < 0).mean()
    streak = mx = 0
    for v in m:
        streak = streak + 1 if v < 0 else 0
        mx = max(mx, streak)
    return {"label": label, "n": len(t), "akhir$": round(eq.iloc[-1]),
            "CAGR%": round(cagr, 1), "maxDD%": round(dd_pct, 1),
            "Ret/DD": round(cagr / abs(dd_pct), 2) if dd_pct else np.nan,
            "bln_merah%": round(red), "merah_beruntun": mx,
            "bln_terburuk$": round(m.min())}


def main():
    df1m = load_1m()
    cache = {}
    sigs = {}
    print("Menghasilkan sinyal keranjang ...")
    for tf, me, sb, tpr, sess in BASKET:
        if tf not in cache:
            cache[tf] = df1m.resample(tf, label="left", closed="left").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
        d = cache[tf]
        key = f"s{sb}TP{tpr:g}"
        sigs[key] = signals(d, supertrend(d, 10, me), sb, tpr, SESSIONS[sess])
        print(f"  {key}: {len(sigs[key])} sinyal")

    out = []
    print("\n" + "=" * 112)
    print("A. SATU KONFIGURASI (s20TP3) — pengaruh FILTER SL LEBAR")
    print("=" * 112)
    base = sigs["s20TP3"]
    for cap in RISK_CAPS:
        t = simulate(base, cap)
        r = report(t, f"cap {cap*100:.1f}%" if cap else "tanpa filter")
        if r:
            r["dilewati"] = len(base) - r["n"]
            out.append(("A", r))
            print(f"  {r['label']:16} n={r['n']:5} (lewat {r['dilewati']:4})  "
                  f"akhir=${r['akhir$']:>6}  CAGR={r['CAGR%']:>6.1f}%  maxDD={r['maxDD%']:>6.1f}%  "
                  f"Ret/DD={r['Ret/DD']:>5.2f}  merah={r['bln_merah%']:>3}%  beruntun={r['merah_beruntun']}")

    print("\n" + "=" * 112)
    print("B. ENSEMBLE 4 konfigurasi bertetangga (modal dibagi 4, setara)")
    print("=" * 112)
    for cap in RISK_CAPS:
        combined = []
        for k, s in sigs.items():
            t = simulate(s, cap, risk_target=0.01)
            if t is not None:
                x = t[["t_in", "pnl"]].copy()
                x["pnl"] /= len(sigs)          # bobot setara
                combined.append(x)
        if not combined:
            continue
        c = pd.concat(combined).sort_values("t_in").reset_index(drop=True)
        c["eq"] = CAPITAL0 + c.pnl.cumsum()
        c["lot"] = MIN_LOT
        r = report(c, f"cap {cap*100:.1f}%" if cap else "tanpa filter")
        if r:
            out.append(("B", r))
            print(f"  {r['label']:16} n={r['n']:5}  akhir=${r['akhir$']:>6}  "
                  f"CAGR={r['CAGR%']:>6.1f}%  maxDD={r['maxDD%']:>6.1f}%  "
                  f"Ret/DD={r['Ret/DD']:>5.2f}  merah={r['bln_merah%']:>3}%  beruntun={r['merah_beruntun']}")

    print("\n" + "=" * 112)
    print("RINGKASAN — diurut Return/DD (makin tinggi makin layak; <0.5 tidak layak jalan)")
    print("=" * 112)
    df = pd.DataFrame([{"blok": b, **r} for b, r in out]).sort_values("Ret/DD", ascending=False)
    print(df[["blok", "label", "n", "akhir$", "CAGR%", "maxDD%", "Ret/DD",
              "bln_merah%", "merah_beruntun", "bln_terburuk$"]].to_string(index=False))
    df.to_csv(r"C:\Quant\_MONITOR\eterna_bestpractice.csv", index=False)
    print("\nDisimpan: C:\\Quant\\_MONITOR\\eterna_bestpractice.csv")


if __name__ == "__main__":
    main()
