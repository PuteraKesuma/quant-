"""ETERNA fase-20: KENAPA PROFITNYA TIDAK RATA PER BULAN?

User bertanya kenapa bulanannya timpang (Jan +1029, Feb +24, Mar +787, Apr -91...).
Ini bukan cacat konfigurasi — ini SIFAT struktural sistem TP 1:4. Skrip ini membuktikannya
dengan angka dan menunjukkan harga dari mencoba meratakannya.

Diuji:
  A. KONSENTRASI — berapa persen profit datang dari segelintir trade terbaik?
  B. TANPA trade terbaik — apa yang tersisa kalau 3/5/10 trade teratas dihapus?
  C. RATA vs RETURN — TP rendah (1:1, 1:1.5, 1:2) bikin bulanan lebih rata, tapi berapa
     harganya? Diukur pakai simpangan baku bulanan + % bulan hijau + Ret/DD.

Jalankan: python research/eterna_concentration.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"
LOT, CONTRACT, COST = 0.01, 100.0, 0.50
CAPITAL, MIN_SL = 1000.0, 0.30
P, MULT_E, MULT_T = 14, 1.8, 3.8


def load_h1():
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
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


def run(h, tp_r):
    st_e, st_t = supertrend(h, P, MULT_E), supertrend(h, P, MULT_T)
    sd = st_e.where(st_e != st_e.shift(1)).shift(1).to_numpy()
    td = st_t.shift(1).to_numpy()
    o, hi, lo = h["open"].to_numpy(), h["high"].to_numpy(), h["low"].to_numpy()
    slo = h["low"].rolling(P).min().shift(1).to_numpy()
    shi = h["high"].rolling(P).max().shift(1).to_numpy()
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
    t["R"] = t.pnl / (t.risk * LOT * CONTRACT)
    return t.set_index("t_in")


def main():
    h = load_h1()
    t = run(h, 4.0)
    net = t.pnl.sum()
    print("=" * 100)
    print("A. KONSENTRASI PROFIT — 5,5 tahun, ATR 14, TP 1:4")
    print("=" * 100)
    s = t.pnl.sort_values(ascending=False)
    print(f"  Total {len(t)} trade, net ${net:,.0f}\n")
    for k in (3, 5, 10, 20, 30):
        share = 100 * s.head(k).sum() / net
        print(f"  {k:>3} trade TERBAIK ({100*k/len(t):4.1f}% dari semua trade) "
              f"menyumbang {share:5.1f}% dari seluruh profit")
    print()
    print(f"  Trade yang untung : {int((t.pnl>0).sum())} ({100*(t.pnl>0).mean():.0f}%)")
    print(f"  Median trade      : ${t.pnl.median():+,.2f}")
    print(f"  Rata-rata menang  : ${t.loc[t.pnl>0,'pnl'].mean():+,.2f}")
    print(f"  Rata-rata kalah   : ${t.loc[t.pnl<=0,'pnl'].mean():+,.2f}")
    print(f"  Trade terbesar    : ${t.pnl.max():+,.2f} ({100*t.pnl.max()/net:.1f}% dari net)")

    print("\n" + "=" * 100)
    print("B. KALAU TRADE TERBAIK HILANG (mis. sistem mati / kamu ragu masuk)")
    print("=" * 100)
    for k in (1, 3, 5, 10):
        rest = t.pnl.sort_values(ascending=False).iloc[k:]
        print(f"  tanpa {k:>2} trade teratas -> net ${rest.sum():>7,.0f}  "
              f"({100*rest.sum()/net:5.1f}% dari asli)")
    print("\n  Inilah kenapa sistem HARUS dibiarkan jalan terus. Melewatkan beberapa")
    print("  trade besar saja sudah cukup mengubah tahun hijau jadi tahun datar.")

    print("\n" + "=" * 100)
    print("C. HARGA DARI 'MERATAKAN' — TP rendah = lebih rata tapi lebih miskin")
    print("=" * 100)
    print(f"{'TP':>8} {'n':>5} {'WR%':>5} {'net$':>8} {'maxDD%':>8} {'Ret/DD':>7} "
          f"{'bln hijau%':>11} {'sd bulanan':>11} {'bln terbaik':>12}")
    for tp_r in (1.0, 1.5, 2.0, 3.0, 4.0, 5.0):
        x = run(h, tp_r)
        if len(x) < 60:
            continue
        eq = CAPITAL + x.pnl.cumsum()
        dd = ((eq - eq.cummax()) / eq.cummax()).min() * 100
        m = x.pnl.resample("ME").sum(); m = m[m != 0]
        yrs = (x.index[-1] - x.index[0]).days / 365.25
        thn = 100 * (x.pnl.sum() / yrs) / CAPITAL
        print(f"{'1:'+format(tp_r,'g'):>8} {len(x):5} {100*(x.pnl>0).mean():5.0f} "
              f"{x.pnl.sum():8.0f} {dd:8.1f} {thn/abs(dd) if dd else 0:7.2f} "
              f"{100*(m>0).mean():11.0f} {m.std():11.0f} {m.max():12.0f}")
    print("\n  'sd bulanan' = simpangan baku PnL bulanan. Makin kecil = makin rata.")
    print("  Perhatikan: TP rendah memang meratakan, tapi Ret/DD-nya jatuh.")


if __name__ == "__main__":
    main()
