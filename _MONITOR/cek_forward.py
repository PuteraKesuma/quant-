"""Pemantau forward test — kemajuan vs harapan, dan cek kriteria HENTIKAN.

Kriteria ditetapkan di _DOC/forward_test.md SEBELUM hasil apa pun terlihat.
Skrip ini hanya membacanya kembali dan mengukur, tidak menafsirkan ulang.
"""
import sys, json, datetime as dt
from pathlib import Path
sys.path.insert(0, r"C:\Quant")
import MetaTrader5 as mt5
import pandas as pd

MULAI = pd.Timestamp("2026-08-14", tz="UTC")
MODAL_AWAL = 523.28
STOP_EQUITY = MODAL_AWAL * 0.75          # -25%
STOP_TRADE = MODAL_AWAL * 0.20           # satu trade rugi > 20% akun

SLEEVE = {
    920617: ("ORB",      120),
    920627: ("ETERNA",   106),
    920643: ("SMC H4",    18),
    920644: ("SMC H1",    39),
}

mt5.initialize()
ai = mt5.account_info()
now = pd.Timestamp.now("UTC")
hari = max(1, (now - MULAI).days)
frac = hari / 365.25

print("=" * 84)
print(f"FORWARD TEST  mulai {MULAI:%Y-%m-%d}  ->  hari ke-{hari}")
print("=" * 84)
print(f"  modal awal $%.2f   sekarang balance $%.2f  equity $%.2f  (%+.2f, %+.1f%%)"
      % (MODAL_AWAL, ai.balance, ai.equity, ai.equity - MODAL_AWAL,
         100 * (ai.equity - MODAL_AWAL) / MODAL_AWAL))

deals = mt5.history_deals_get(MULAI.tz_localize(None).to_pydatetime(),
                              (now + pd.Timedelta(days=1)).tz_localize(None).to_pydatetime()) or []
masuk = [d for d in deals if d.entry == 0 and d.magic in SLEEVE]
keluar = [d for d in deals if d.entry == 1 and d.magic in SLEEVE]

print("\n  %-10s %8s %10s %12s %10s" % ("sleeve", "trade", "harapan", "PnL$", "status"))
print("  " + "-" * 56)
total = 0.0
for mg, (nama, per_thn) in SLEEVE.items():
    n = sum(1 for d in masuk if d.magic == mg)
    pnl = sum(d.profit + d.swap + d.commission for d in keluar if d.magic == mg)
    total += pnl
    harap = per_thn * frac
    st = "wajar"
    if harap >= 3:
        if n < harap * 0.5: st = "terlalu SEDIKIT"
        elif n > harap * 1.5: st = "terlalu BANYAK"
    print("  %-10s %8d %10.1f %12.2f %10s" % (nama, n, harap, pnl, st))
print("  " + "-" * 56)
print("  %-10s %8d %10s %12.2f" % ("TOTAL", len(masuk), "", total))

print("\n" + "=" * 84)
print("KRITERIA HENTIKAN (dari _DOC/forward_test.md)")
print("=" * 84)
langgar = []
if ai.equity < STOP_EQUITY:
    langgar.append(f"equity ${ai.equity:.2f} < batas ${STOP_EQUITY:.2f} (-25%)")
rugi_besar = [d for d in keluar if (d.profit + d.swap) < -STOP_TRADE]
if rugi_besar:
    langgar.append(f"{len(rugi_besar)} trade rugi > ${STOP_TRADE:.2f} (20% akun)")
# SMC lebih dari 2 trade/hari?
for mg in (920643, 920644):
    per_hari = {}
    for d in masuk:
        if d.magic != mg:
            continue
        k = pd.Timestamp(d.time, unit="s").normalize()
        per_hari[k] = per_hari.get(k, 0) + 1
    lebih = {k: v for k, v in per_hari.items() if v > 2}
    if lebih:
        langgar.append(f"magic {mg} melewati batas 2 trade/hari: {lebih}")

print("  equity          : $%.2f  (batas $%.2f)  %s" % (
    ai.equity, STOP_EQUITY, "OK" if ai.equity >= STOP_EQUITY else "DILANGGAR"))
print("  rugi per trade  : %d trade > $%.2f  %s" % (
    len(rugi_besar), STOP_TRADE, "OK" if not rugi_besar else "DILANGGAR"))
print("  batas 2/hari SMC: %s" % ("OK" if not any("2 trade/hari" in x for x in langgar)
                                  else "DILANGGAR"))

print("\n  Paritas & mekanisme TIDAK bisa dicek otomatis dari sini - jalankan")
print("  research/smc_paritas.py, dan periksa log saat order pertama muncul.")

print("\n" + "=" * 84)
if langgar:
    print("  >> HENTIKAN: " + " | ".join(langgar))
else:
    print("  >> LANJUTKAN. Tidak ada kriteria hentikan yang dilanggar.")
    print("     Rugi dalam batas wajar BUKAN alasan berhenti - backtest sendiri")
    print("     memperkirakan 85% hari tanpa trade SMC.")
print("=" * 84)
mt5.shutdown()
