"""FASE-29: SLEEVE BARU dari HIPOTESIS EKONOMI (bukan sapuan parameter).

Audit portofolio fase-28 membuktikan korelasi antar-mekanisme memang mendekati NOL
(rata-rata +0.03) — jadi diversifikasi tersedia. Yang kurang adalah sleeve BERMUTU:
dari 5 sleeve, hanya 2 yang Sharpe-nya positif.

Fase ini menambah sleeve dari anomali yang SUDAH terdokumentasi di literatur keuangan,
dengan disiplin yang sengaja dibuat ketat:

  1. Alasan ekonomi ditulis SEBELUM hasil dilihat.
  2. Parameter diambil dari definisi anomali, TIDAK disapu. Tidak ada optimasi.
  3. SEMUA sleeve yang diuji dilaporkan, termasuk yang gagal — tidak ada cherry-pick.
     N percobaan = jumlah sleeve, dan itu dipakai apa adanya di Deflated Sharpe.

Kenapa seketat ini: eterna gagal DSR (0.0061) bukan karena strateginya jelek, tapi
karena kami menyapu ~1900 konfigurasi untuk menemukannya. Kesalahan itu tidak diulang.

--------------------------------------------------------------------------------------
SLEEVE 1 — OVERNIGHT EQUITY PREMIUM (NAS100)
  Hipotesis: return indeks saham terkonsentrasi di luar jam bursa. Kompensasi risiko
  memegang posisi saat likuiditas tipis dan berita (laporan laba, data makro) rilis
  di luar sesi. Literatur: Cooper, Cliff & Gulen (2008); Lachance (2021).
  Aturan: LONG di penutupan sesi NY (20:00 UTC), keluar di pembukaan (13:30 UTC).
  Tanpa parameter sama sekali.

SLEEVE 2 — TURN-OF-MONTH (NAS100)
  Hipotesis: arus masuk dana pensiun & gaji terkonsentrasi di pergantian bulan,
  menciptakan tekanan beli musiman. Literatur: Ariel (1987); Lakonishok & Smidt (1988).
  Aturan: LONG dari penutupan hari bursa TERAKHIR bulan, keluar di penutupan hari
  bursa KE-3 bulan berikutnya. Jendela dari literatur, tidak disapu.

SLEEVE 3 — GOTOBI (USDJPY)
  Hipotesis: perusahaan Jepang menyelesaikan tagihan di tanggal kelipatan 5 ("gotobi"),
  menciptakan permintaan USD terjadwal yang memuncak di fixing Tokyo 09:55 JST.
  Literatur: Ranaldo (2009); riset user sendiri (research/gotobi_usdjpy.py).
  Aturan: pada tanggal 5/10/15/20/25/30, LONG USDJPY dari 21:00 UTC hari sebelumnya
  sampai 01:00 UTC (setelah fixing). Tanggal & jam dari definisi anomali.

Jalankan: python research/sleeves_baru.py
"""
import warnings
warnings.filterwarnings("ignore")

import duckdb
import numpy as np
import pandas as pd
from scipy import stats

RAW = r"C:\Quant\data\Level_0_Raw"
CAPITAL, COST, LOT = 1000.0, 0.50, 0.01
CONTRACT = {"XAUUSD": 100.0, "NAS100": 10.0, "USDJPY": 100000.0}


def load_1m(sym):
    con = duckdb.connect(rf"{RAW}\{sym}_1m.duckdb", read_only=True)
    df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
    con.close()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    return df.set_index("ts")


def bar_at(m1, day, hh, mm):
    """Harga pembukaan bar 1m pada jam:menit tertentu di hari itu (None kalau tak ada)."""
    try:
        seg = m1.loc[f"{day}"]
    except KeyError:
        return None
    s = seg[(seg.index.hour == hh) & (seg.index.minute == mm)]
    return float(s["open"].iloc[0]) if len(s) else None


def pnl(trades, sym):
    """PnL dalam USD.

    KOREKSI: untuk pair ber-quote JPY (USDJPY), selisih harga ada dalam YEN, bukan USD.
    Harus dibagi harga keluar untuk dikonversi. Versi pertama skrip ini mengalikan
    langsung seperti pair quote-USD -> membesar ~150x, yang membuat maxDD GOTOBI
    tampak -70%. Sharpe & DSR TIDAK terpengaruh (keduanya kebal skala), jadi vonis
    'tidak lolos' tetap sama — tapi angka CAGR/DD-nya sebelumnya keliru.
    """
    if not trades:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([], tz="UTC"))
    t = pd.DataFrame(trades, columns=["ts", "dir", "px_in", "px_out"])
    gross = (t.px_out - t.px_in) * t.dir * LOT * CONTRACT[sym]
    if sym.endswith("JPY"):
        gross = gross / t.px_out
    return pd.Series((gross - COST).values, index=pd.DatetimeIndex(t.ts))


# ---------------------------------------------------------------- SLEEVE 1
def sleeve_overnight(m1):
    """LONG dari 20:00 UTC (tutup sesi NY) ke 13:30 UTC (buka sesi NY) berikutnya."""
    h = m1.resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    closes = h[h.index.hour == 20]["open"]
    opens = h[h.index.hour == 13]["open"]
    op = {ts.date(): v for ts, v in opens.items()}
    out = []
    for ts, px_in in closes.items():
        # keluar di pembukaan hari BURSA berikutnya
        for k in range(1, 5):
            d = (ts + pd.Timedelta(days=k)).date()
            if d in op:
                out.append((ts, 1, px_in, op[d]))
                break
    return pnl(out, "NAS100")


# ---------------------------------------------------------------- SLEEVE 2
def sleeve_turn_of_month(m1):
    """LONG dari tutup hari bursa terakhir bulan -> tutup hari bursa ke-3 bulan baru."""
    d1 = m1.resample("1D").agg({"open": "first", "close": "last"}).dropna()
    d1 = d1[d1["close"].notna()]
    days = list(d1.index)
    out = []
    for i, ts in enumerate(days[:-4]):
        nxt = days[i + 1]
        if nxt.month == ts.month:
            continue                      # bukan hari terakhir bulan
        ex = days[min(i + 3, len(days) - 1)]
        out.append((ts, 1, float(d1.loc[ts, "close"]), float(d1.loc[ex, "close"])))
    return pnl(out, "NAS100")


# ---------------------------------------------------------------- SLEEVE 3
def sleeve_gotobi(m1):
    """LONG USDJPY 21:00 UTC (H-1) -> 01:00 UTC pada tanggal kelipatan 5."""
    h = m1.resample("1h", label="left", closed="left").agg(
        {"open": "first", "close": "last"}).dropna()
    at21 = {ts.date(): float(v) for ts, v in h[h.index.hour == 21]["open"].items()}
    at01 = {ts.date(): float(v) for ts, v in h[h.index.hour == 1]["open"].items()}
    out = []
    for d, px_out in sorted(at01.items()):
        if d.day not in (5, 10, 15, 20, 25, 30):
            continue
        prev = d - pd.Timedelta(days=1)
        for k in range(1, 4):
            p = (d - pd.Timedelta(days=k))
            if p in at21:
                out.append((pd.Timestamp(p, tz="UTC"), 1, at21[p], px_out))
                break
    return pnl(out, "USDJPY")


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
    st_ = mx = 0
    for v in m:
        st_ = st_ + 1 if v < 0 else 0
        mx = max(mx, st_)
    return {"sleeve": label, "bln": len(m), "CAGR%": round(100 * cagr, 1),
            "maxDD%": round(100 * dd, 1), "Sharpe": round(sh, 2),
            "Calmar": round(cagr / abs(dd), 2) if dd else np.nan,
            "hijau%": round(100 * (m > 0).mean()), "beruntun": mx}


def main():
    print("Membangun sleeve baru dari hipotesis ekonomi ...\n")
    nas = load_1m("NAS100")
    jpy = load_1m("USDJPY")

    sl = {}
    for name, fn, arg in (("OVERNIGHT_nas", sleeve_overnight, nas),
                          ("TURNMONTH_nas", sleeve_turn_of_month, nas),
                          ("GOTOBI_jpy",    sleeve_gotobi,      jpy)):
        s = fn(arg)
        sl[name] = s
        print(f"  {name:<15} {len(s):>5} trade   net ${s.sum():>8.0f}", flush=True)

    mon = pd.DataFrame({k: v.resample("ME").sum() for k, v in sl.items()}).fillna(0.0)
    mon = mon.loc[(mon != 0).any(axis=1)]
    N = len(sl)          # HANYA sebanyak sleeve yang diuji — tidak ada sapuan

    print("\n" + "=" * 96)
    print(f"HASIL SEMUA SLEEVE BARU (dilaporkan seluruhnya, N percobaan = {N})")
    print("=" * 96)
    rows = [metrics(mon[c], c) for c in mon.columns]
    print(pd.DataFrame([r for r in rows if r]).to_string(index=False))

    print("\n" + "=" * 96)
    print("DEFLATED SHARPE tiap sleeve")
    print("=" * 96)
    for c in mon.columns:
        s, s0, p = dsr((mon[c] / CAPITAL).values, N)
        von = "LOLOS" if p >= 0.95 else ("BATAS" if p >= 0.90 else "TIDAK LOLOS")
        print(f"  {c:<15} Sharpe {s*np.sqrt(12):+.2f}  ambang {s0*np.sqrt(12):+.2f}  "
              f"DSR {p:.4f}  -> {von}")

    print("\n" + "=" * 96)
    print("KORELASI antar sleeve baru")
    print("=" * 96)
    print(mon.corr().round(2).to_string())

    mon.to_csv(r"C:\Quant\_MONITOR\sleeves_baru_monthly.csv")
    print("\nDisimpan: C:\\Quant\\_MONITOR\\sleeves_baru_monthly.csv")


if __name__ == "__main__":
    main()
