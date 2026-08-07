"""ETERNA (EA EternaBot V.2) — uji sinyal TELANJANG, tanpa martingale.

Port logika EA EternaBot V.2.mq5 (dual Supertrend, sumber TikTok @reeztradefx) ke Python
untuk diuji di data XAUUSD 1m kita (5.5 tahun, 2021-2026).

Kenapa tanpa martingale: martingale TIDAK menciptakan edge, dia hanya mengubah distribusi
(banyak menang kecil -> sesekali kalah besar). Riset kita sendiri (research/semi_marti_signal.py,
commit 62ea652) sudah membuktikan EA sejenis: sinyal telanjangnya rugi tiap tahun, "akurasinya
adalah ilusi martingale". Jadi uji yang jujur = sinyal mentah, lot tetap.

ANTI-LOOKAHEAD (pelajaran Golden, golden_check.py 2026-07-17):
- Supertrend dihitung pada bar TERTUTUP, sinyal dibaca dari shift 1 (seperti EA: CopyBuffer(...,1,1)).
- Eksekusi di OPEN bar berikutnya, bukan close bar sinyal.
- Gate timeframe lebih tinggi (kalau dipakai) WAJIB shift(+1) sebelum reindex.

Jalankan: python research/eterna_baseline.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd

DB = r"C:\Quant\data\Level_0_Raw\XAUUSD_1m.duckdb"

# --- parameter EA (default EternaBot V.2) ---
ATR_PERIOD = 10
MULT_ENTRY = 1.2          # Supertrend entry
MULT_TREND = 3.8          # Supertrend filter tren
SL_POINTS = 1000          # Manual_SL_Points; XAU digits=2 -> 1 poin = 0.01 -> SL = $10.00
POINT = 0.01
LOT = 0.03                # Lotsize default
CONTRACT = 100.0          # 1 lot XAU = 100 oz
COST_USD_PER_TRADE = 0.35 # spread+komisi realistis FBS @0.03 lot (konservatif)

# sesi EA (waktu server broker, FBS = UTC+3 musim panas). Kita uji dalam UTC.
SESSIONS = {
    "all_day":  None,
    "slot1_asia":   (23, 30, 4, 0),    # 02:30-07:00 server ~= 23:30-04:00 UTC
    "slot2_london": (5, 30, 10, 0),    # 08:30-13:00 server ~= 05:30-10:00 UTC
    "slot3_ny":     (11, 30, 16, 0),   # 14:30-19:00 server ~= 11:30-16:00 UTC  <- default EA
}


def load_1m() -> pd.DataFrame:
    con = duckdb.connect(DB, read_only=True)
    df = con.execute("SELECT ts, open, high, low, close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def resample(df1m: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf == "1min":
        return df1m
    o = df1m.resample(tf, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"})
    return o.dropna()


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    # Wilder smoothing (seperti iATR MT5)
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()


def supertrend(df: pd.DataFrame, period: int, mult: float) -> pd.Series:
    """Return +1 (uptrend) / -1 (downtrend) per bar. Implementasi standar Supertrend."""
    a = atr(df, period)
    hl2 = (df["high"] + df["low"]) / 2.0
    upper = (hl2 + mult * a).to_numpy()
    lower = (hl2 - mult * a).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)

    fu = np.full(n, np.nan)
    fl = np.full(n, np.nan)
    dirn = np.ones(n, dtype=int)

    for i in range(1, n):
        if np.isnan(upper[i]) or np.isnan(lower[i]):
            continue
        fu[i] = upper[i] if (np.isnan(fu[i-1]) or upper[i] < fu[i-1] or close[i-1] > fu[i-1]) else fu[i-1]
        fl[i] = lower[i] if (np.isnan(fl[i-1]) or lower[i] > fl[i-1] or close[i-1] < fl[i-1]) else fl[i-1]
        if not np.isnan(fu[i-1]) and close[i] > fu[i]:
            dirn[i] = 1
        elif not np.isnan(fl[i-1]) and close[i] < fl[i]:
            dirn[i] = -1
        else:
            dirn[i] = dirn[i-1]
    return pd.Series(dirn, index=df.index)


def in_session(idx: pd.DatetimeIndex, sess) -> np.ndarray:
    if sess is None:
        return np.ones(len(idx), dtype=bool)
    sh, sm, eh, em = sess
    mins = idx.hour * 60 + idx.minute
    s, e = sh * 60 + sm, eh * 60 + em
    return (mins >= s) & (mins < e) if s < e else (mins >= s) | (mins < e)


def backtest(df: pd.DataFrame, mode: str, sess, use_trend_filter: bool) -> dict:
    """Selalu di pasar / flip pada sinyal entry-Supertrend. Eksekusi di OPEN bar berikutnya.

    mode 'direct'       : flip tiap kali entry-ST berbalik (default EA, 2 arah, tanpa filter)
    mode 'conservative' : hanya entry searah trend-ST
    """
    st_e = supertrend(df, ATR_PERIOD, MULT_ENTRY)
    st_t = supertrend(df, ATR_PERIOD, MULT_TREND)

    # sinyal = bar di mana arah entry-ST BERUBAH; dibaca dari bar tertutup (shift 1)
    flip = (st_e != st_e.shift(1))
    sig_dir = st_e.where(flip)                       # +1 buy, -1 sell pada bar flip
    sig_dir = sig_dir.shift(1)                       # EA baca shift 1 -> bar tertutup
    trend_dir = st_t.shift(1)                        # gate juga dari bar tertutup

    ok_sess = in_session(df.index, sess)
    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    sd = sig_dir.to_numpy()
    td = trend_dir.to_numpy()

    sl_dist = SL_POINTS * POINT
    pos = 0          # +1 long, -1 short, 0 flat
    entry = 0.0
    trades = []
    entry_i = 0

    for i in range(1, len(df)):
        # --- kelola posisi terbuka: cek SL dulu (konservatif) ---
        if pos != 0:
            if pos == 1 and l[i] <= entry - sl_dist:
                trades.append((df.index[entry_i], df.index[i], pos, entry, entry - sl_dist, "SL"))
                pos = 0
            elif pos == -1 and h[i] >= entry + sl_dist:
                trades.append((df.index[entry_i], df.index[i], pos, entry, entry + sl_dist, "SL"))
                pos = 0

        s = sd[i]
        if np.isnan(s):
            continue
        s = int(s)

        if use_trend_filter and not np.isnan(td[i]) and int(td[i]) != s:
            # sinyal melawan tren -> mode konservatif hanya menutup, tidak membalik
            if pos == -s:
                trades.append((df.index[entry_i], df.index[i], pos, entry, o[i], "exit_trend"))
                pos = 0
            continue

        if not ok_sess[i]:
            continue

        if pos == 0:
            pos, entry, entry_i = s, o[i], i
        elif pos != s:
            trades.append((df.index[entry_i], df.index[i], pos, entry, o[i], "flip"))
            pos, entry, entry_i = s, o[i], i

    if not trades:
        return {"n": 0}

    t = pd.DataFrame(trades, columns=["t_in", "t_out", "dir", "px_in", "px_out", "why"])
    t["pnl"] = (t["px_out"] - t["px_in"]) * t["dir"] * LOT * CONTRACT - COST_USD_PER_TRADE
    t["R"] = t["pnl"] / (sl_dist * LOT * CONTRACT)

    wins, losses = t.loc[t.pnl > 0, "pnl"], t.loc[t.pnl <= 0, "pnl"]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else np.inf
    eq = t["pnl"].cumsum()
    dd = (eq - eq.cummax()).min()
    yearly = t.groupby(t["t_in"].dt.year)["pnl"].sum()

    return {
        "n": len(t), "net": t.pnl.sum(), "pf": pf,
        "wr": (t.pnl > 0).mean() * 100, "avgR": t.R.mean(),
        "maxDD": dd, "yearly": yearly,
        "green_years": int((yearly > 0).sum()), "tot_years": len(yearly),
    }


def main():
    print("Memuat XAUUSD 1m ...")
    df1m = load_1m()
    print(f"  {len(df1m):,} bar  {df1m.index[0]} .. {df1m.index[-1]}\n")

    rows = []
    for tf_label, tf in [("M1", "1min"), ("M5", "5min"), ("M15", "15min"),
                         ("M30", "30min"), ("H1", "1h"), ("H4", "4h")]:
        d = resample(df1m, tf)
        if len(d) < 500:
            continue
        for sess_name, sess in SESSIONS.items():
            for mode, filt in [("direct", False), ("conservative", True)]:
                r = backtest(d, mode, sess, filt)
                if r["n"] < 30:
                    continue
                rows.append({
                    "TF": tf_label, "sesi": sess_name, "mode": mode,
                    "n": r["n"], "net$": round(r["net"], 0), "PF": round(r["pf"], 2),
                    "WR%": round(r["wr"], 1), "avgR": round(r["avgR"], 3),
                    "maxDD$": round(r["maxDD"], 0),
                    "thn_hijau": f"{r['green_years']}/{r['tot_years']}",
                })
                print(f"  {tf_label:4} {sess_name:14} {mode:13} n={r['n']:6}  "
                      f"net={r['net']:9.0f}  PF={r['pf']:5.2f}  avgR={r['avgR']:+.3f}  "
                      f"hijau={r['green_years']}/{r['tot_years']}")

    out = pd.DataFrame(rows).sort_values("avgR", ascending=False)
    out.to_csv(r"C:\Quant\_MONITOR\eterna_baseline.csv", index=False)
    print("\n" + "=" * 100)
    print("TOP 15 berdasarkan avgR (sinyal telanjang, TANPA martingale):")
    print(out.head(15).to_string(index=False))
    print("\nJUMLAH konfigurasi dengan avgR > 0 :",
          int((out["avgR"] > 0).sum()), "dari", len(out))
    print("Disimpan: C:\\Quant\\_MONITOR\\eterna_baseline.csv")


if __name__ == "__main__":
    main()
