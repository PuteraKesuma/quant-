"""ORB di seluruh riwayat 5,5 tahun - PENYEBUT untuk angka Juni-Juli 2026.

LATAR: research/andai_juni_juli.py menemukan ORB rugi -$205 di Juni-Juli 2026 dan aku
sempat melaporkannya sebagai temuan. User bertanya "aneh ORB bisa rugi" - pertanyaan
yang benar, karena aku mengutip angka mentah TANPA mengukur dulu apakah dua bulan
seperti itu wajar. Skrip ini mengukur penyebutnya.

Uji ini memisahkan dua kemungkinan:
  - kalau model mereproduksi klaim aslinya -> Juni-Juli memang jendela buruk
  - kalau model rugi di mana-mana          -> modelnya yang salah, -$205 tidak sah

HASIL (2026-08-15):
  689 trade, 2021-2026. Net +$466,49. PF 1,22. WR 43%. Ekspektasi +0,1045 R/trade.
  6 dari 6 tahun HIJAU -> model mereproduksi klaim aslinya -> model SAH.

  Juni-Juli tiap tahun: +35,86 / +11,88 / +3,06 / +44,47 / +11,39 / -79,09 (2026)
  Juni 2026 = BULAN TERBURUK sepanjang 64 bulan.
  Dari 63 jendela 2-bulanan, NOL yang lebih buruk dari -$205. Terburuk: -$83,88.

KESIMPULAN YANG HARUS DIINGAT:
  ORB rugi BUKAN karena rusak, tapi karena edge-nya TIPIS - ekspektasi cuma +0,105R
  dan 33% bulan memang merah. Satu dari tiga bulan rugi ADALAH desainnya. Menilai
  sleeve seperti ini dari jendela 2 bulan akan selalu menyesatkan.

  Dari tiga sleeve di buku, ORB justru yang bukti statistiknya PALING KUAT:
    ORB    689 trade, 5,5 tahun, 6/6 tahun hijau, PF 1,22
    SMC    DSR 0,629 (< ambang 0,95), 0,07 trade/hari
    ETERNA DSR 0,0061, satu trade = 86% laba Juni-Juli

  User tetap memilih ORB mati (2026-08-15) setelah bukti ini disampaikan. Itu
  keputusannya; yang berubah adalah keputusan itu kini punya penyebutnya.

Dipakai duckdb NAS100 (2021-2026) karena itulah data yang dipakai riset aslinya.
Instrumennya beda level dari US100 FBS (terukur beda rata-rata 51,6 poin), tapi ORB
itu strategi RELATIF terhadap range harian, jadi bentuk edge-nya tetap sebanding.

Jalankan: python research/orb_jangka_panjang.py
"""
import sys, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, r"C:\Quant")
import duckdb, numpy as np, pandas as pd
from pipeline.fetch.base_fetcher import load_config

cfg = load_config()
sp = [s for s in cfg["live"]["strategies"] if s.get("name") == "orb30_nas"][0]
p = sp["params"]
RNG = int(p["range_minutes"]); TPM = float(p["tp_mult"]); SLM = float(p["sl_mult"])
BE = p.get("breakeven_r"); NSMA = int(p["trend_sma"])
EH, EM = map(int, p["session_end_utc"].split(":"))
PER_POIN, BIAYA = 0.10, 0.30

con = duckdb.connect(r"C:\Quant\data\Level_0_Raw\NAS100_1m.duckdb", read_only=True)
df = con.execute("SELECT ts,open,high,low,close FROM ohlcv ORDER BY ts").df()
con.close()
df["ts"] = pd.to_datetime(df["ts"], utc=True)
m1 = df.set_index("ts")[["open", "high", "low", "close"]].sort_index()
print("data %s .. %s (%d bar)" % (m1.index[0].date(), m1.index[-1].date(), len(m1)))

harian = m1["close"].resample("1D").last().dropna()
sma = harian.rolling(NSMA).mean()

tr = []
for tgl, g in m1.groupby(m1.index.normalize()):
    lalu = harian.index[harian.index < tgl]
    if len(lalu) == 0 or pd.isna(sma.get(lalu[-1], np.nan)):
        continue
    d0 = lalu[-1]
    arah_tren = 1 if harian[d0] > sma[d0] else -1
    # DST: musim panas AS buka 13:30 UTC, musim dingin 14:30
    bulan = tgl.month
    jam = 13 if 3 <= bulan <= 10 else 14
    buka = tgl.replace(hour=jam, minute=30)
    rng = g.loc[buka:buka + pd.Timedelta(minutes=RNG - 1)]
    if len(rng) < RNG // 2:
        continue
    hi, lo = rng["high"].max(), rng["low"].min()
    uk = hi - lo
    if uk <= 0:
        continue
    post = g.loc[buka + pd.Timedelta(minutes=RNG):tgl.replace(hour=EH, minute=EM)]
    if post.empty:
        continue
    sisi = 0
    for ts, b in post.iterrows():
        if b["high"] > hi: sisi, mts = 1, ts; break
        if b["low"] < lo:  sisi, mts = -1, ts; break
    if sisi == 0 or sisi != arah_tren:
        continue
    ent = hi if sisi == 1 else lo
    risk = SLM * uk
    sl = ent - risk if sisi == 1 else ent + risk
    tp = ent + TPM * uk if sisi == 1 else ent - TPM * uk
    armed = False; out = None
    for ts, b in post.loc[mts:].iterrows():
        if sisi == 1:
            if BE is not None and not armed and (b["high"] - ent) >= BE * risk: armed = True
            if armed and b["low"] <= ent: out = (ent, "BE"); break
            if b["low"] <= sl:  out = (sl, "SL"); break
            if b["high"] >= tp: out = (tp, "TP"); break
        else:
            if BE is not None and not armed and (ent - b["low"]) >= BE * risk: armed = True
            if armed and b["high"] >= ent: out = (ent, "BE"); break
            if b["high"] >= sl: out = (sl, "SL"); break
            if b["low"] <= tp:  out = (tp, "TP"); break
    if out is None:
        out = (post.loc[mts:].iloc[-1]["close"], "SESI")
    kpx, sebab = out
    tr.append({"tgl": tgl, "thn": tgl.year, "sisi": sisi, "sebab": sebab,
               "R": (kpx - ent) * sisi / risk,
               "pnl": (kpx - ent) * sisi * PER_POIN - BIAYA, "range": uk})

t = pd.DataFrame(tr)
print("\n%d trade dari %s sampai %s" % (len(t), t.tgl.min().date(), t.tgl.max().date()))

print("\n=== PER TAHUN ===")
print("%-6s %6s %10s %8s %7s %6s" % ("thn", "trade", "net $", "PF", "WR%", "avg R"))
for y, g in t.groupby("thn"):
    menang = g[g.pnl > 0].pnl.sum(); kalah = -g[g.pnl < 0].pnl.sum()
    pf = menang / kalah if kalah > 0 else float("inf")
    print("%-6d %6d %+10.2f %8.2f %6.0f%% %+6.3f" % (
        y, len(g), g.pnl.sum(), pf, 100 * (g.pnl > 0).mean(), g.R.mean()))

menang = t[t.pnl > 0].pnl.sum(); kalah = -t[t.pnl < 0].pnl.sum()
print("\nKESELURUHAN: net %+.2f  PF %.2f  WR %.0f%%  ekspektasi %+.4f R/trade" % (
    t.pnl.sum(), menang / kalah, 100 * (t.pnl > 0).mean(), t.R.mean()))
print("sebab keluar:", dict(t.sebab.value_counts()))

print("\n=== JUNI-JULI TIAP TAHUN (jendela yang dipersoalkan) ===")
t["bln"] = t.tgl.dt.month
jj = t[t.bln.isin([6, 7])]
print("%-6s %6s %10s %7s" % ("thn", "trade", "net $", "WR%"))
for y, g in jj.groupby("thn"):
    print("%-6d %6d %+10.2f %6.0f%%" % (y, len(g), g.pnl.sum(), 100 * (g.pnl > 0).mean()))
print("rata-rata Juni-Juli: %+.2f per tahun" % jj.groupby("thn").pnl.sum().mean())

print("\n=== APAKAH 2 BULAN RUGI ITU NORMAL? ===")
bulanan = t.set_index("tgl").pnl.resample("ME").sum()
print("%d bulan: %d hijau, %d merah (%.0f%% merah)" % (
    len(bulanan), (bulanan > 0).sum(), (bulanan < 0).sum(), 100 * (bulanan < 0).mean()))
print("bulan terburuk %+.2f | persentil-10 %+.2f | median %+.2f" % (
    bulanan.min(), bulanan.quantile(0.10), bulanan.median()))
pas2 = bulanan.rolling(2).sum().dropna()
print("jendela 2-bulan: %d dari %d (%.0f%%) lebih buruk dari -$205" % (
    (pas2 <= -205).sum(), len(pas2), 100 * (pas2 <= -205).mean()))
print("2-bulan terburuk sepanjang sejarah: %+.2f" % pas2.min())
