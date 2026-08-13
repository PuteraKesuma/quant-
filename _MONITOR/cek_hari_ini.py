import sys, datetime as dt, json, urllib.request; sys.path.insert(0, r"C:\Quant")
import MetaTrader5 as mt5, pandas as pd
from pipeline.fetch.base_fetcher import load_config
from pipeline.live.smc_limit_manager import SmcLimitManager
cfg = load_config(); mt5.initialize()
now = pd.Timestamp.now("UTC")
print("SEKARANG %s UTC (broker %s)" % (now.strftime("%Y-%m-%d %H:%M"),
                                       (now+pd.Timedelta(hours=3)).strftime("%H:%M")))
print("="*80)
print("AKTIVITAS HARI INI (deal sejak 00:00 UTC)")
d = mt5.history_deals_get(dt.datetime.utcnow()-dt.timedelta(hours=26), dt.datetime.utcnow()+dt.timedelta(hours=6)) or []
hari = [x for x in d if pd.Timestamp(x.time,unit='s') >= pd.Timestamp(now.date())+pd.Timedelta(hours=3)]
nama = {920617:"ORB", 920627:"ETERNA", 920644:"SMC"}
if not hari:
    print("  (tidak ada deal hari ini)")
for x in hari:
    print("  %s  %-7s %-4s %.2f  profit %+.2f  '%s'" % (
        pd.Timestamp(x.time,unit='s').strftime("%H:%M"), nama.get(x.magic,x.magic),
        {0:"BUY",1:"SELL"}.get(x.type,"?"), x.price, x.profit, x.comment))
print("\nSTATUS TIAP SLEEVE SEKARANG")
print("-"*80)
h = json.load(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5))
sig = json.load(urllib.request.urlopen("http://127.0.0.1:8000/signals?symbol=XAUUSD", timeout=10))
e = [x for x in sig["signals"] if x["strategy"]=="eterna_xau"]
print("  ETERNA : brain %s, sinyal %s (magic %d)" % (
    h["status"], e[0]["action"] if e else "?", e[0]["magic"] if e else 0))
nyh = now.replace(hour=13, minute=30, second=0, microsecond=0)
print("  ORB    : sesi NY 13:30-14:30 UTC -> %s. Sesi berikutnya %s" % (
    "SUDAH LEWAT hari ini" if now > nyh+pd.Timedelta(hours=1) else "belum/berlangsung",
    (nyh+pd.Timedelta(days=1)).strftime("%m-%d %H:%M UTC")))
sp = [s for s in cfg["live"]["strategies"] if s.get("name")=="smc_xau_h1"][0]
m = SmcLimitManager(cfg, sp); st = m._setup_terkini(m._bar_selesai())
if st is None:
    print("  SMC    : tidak ada zona aktif (normal, ~81 zona/tahun = 1 tiap 4-5 hari)")
else:
    siap, alasan = m._konfirmasi_m5(st)
    print("  SMC    : zona %s %.2f -> %s" % (
        "SELL" if st["arah"]==-1 else "BUY", st["price"], alasan))
a = mt5.account_info()
print("\n  balance %.2f  equity %.2f  posisi %d  pending %d  algo %s" % (
    a.balance, a.equity, len(mt5.positions_get() or []), len(mt5.orders_get() or []),
    mt5.terminal_info().trade_allowed))
mt5.shutdown()
