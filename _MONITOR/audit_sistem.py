"""AUDIT KEAMANAN SISTEM — apa yang bisa membuatnya gagal atau rugi."""
import sys, json, subprocess, urllib.request, os
from pathlib import Path
sys.path.insert(0, r"C:\Quant")
import MetaTrader5 as mt5, pandas as pd
from pipeline.fetch.base_fetcher import load_config

TEMUAN = []          # (tingkat, pesan)  tingkat: OK / PERHATIAN / BAHAYA
def lapor(t, k, v, catatan=""):
    print("  [%-9s] %-30s %s%s" % (t, k, v, ("  -- " + catatan) if catatan else ""))
    if t != "OK":
        TEMUAN.append((t, "%s: %s %s" % (k, v, catatan)))

def ps(cmd):
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                       capture_output=True, text=True, timeout=90)
    return r.stdout.strip()

cfg = load_config()
S = {s.get("name"): s for s in cfg["live"]["strategies"]}

print("=" * 92); print("A. PROSES & PENGAWASAN"); print("=" * 92)
n_py = ps("(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*pipeline.live*' } | Measure-Object).Count")
lapor("OK" if n_py == "6" else "BAHAYA", "proses trading", n_py + " / 6")
mods = ps("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*pipeline.live*' } | ForEach-Object { ($_.CommandLine -split 'pipeline\\.live\\.')[-1] }")
for w in ("run_server", "xau_executor", "orb_stop_manager", "smc_limit_manager", "advisor", "monthly_governor"):
    lapor("OK" if w in mods else "BAHAYA", "  " + w, "hidup" if w in mods else "MATI")

st = ps("(Get-ScheduledTask -TaskName 'Quant Watchdog').State")
lapor("OK" if st == "Running" else "BAHAYA", "task 'Quant Watchdog'", st)
nxt = ps("(Get-ScheduledTaskInfo -TaskName 'Quant Watchdog').NextRunTime")
lapor("OK" if nxt else "PERHATIAN", "jadwal berikutnya", nxt or "KOSONG",
      "" if nxt else "repetisi mungkin tidak aktif")
alive = Path(r"C:\Quant\_MONITOR\watchdog_alive.txt")
if alive.exists():
    ts = pd.Timestamp(alive.read_text().split(" UTC")[0], tz="UTC")
    umur = (pd.Timestamp.now("UTC") - ts).total_seconds()
    lapor("OK" if umur < 120 else "BAHAYA", "detak watchdog", "%.0f detik lalu" % umur)

print("\n" + "=" * 92); print("B. BERTAHAN SETELAH REBOOT"); print("=" * 92)
al = ps("(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon').AutoAdminLogon")
lapor("OK" if al == "1" else "BAHAYA", "AutoAdminLogon", al or "kosong")
du = ps("(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon').DefaultUserName")
lapor("OK" if du else "PERHATIAN", "DefaultUserName", du or "kosong")
dp = ps("(Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon').DefaultPassword")
lapor("OK" if not dp else "BAHAYA", "DefaultPassword di registry",
      "tidak ada (benar)" if not dp else "ADA - password terbuka!")
trg = ps("(Get-ScheduledTask -TaskName 'Quant Watchdog').Triggers.Count")
lapor("OK" if trg and int(trg or 0) >= 2 else "PERHATIAN", "pemicu task", trg + " pemicu")

print("\n" + "=" * 92); print("C. MT5 & AKUN"); print("=" * 92)
mt5.initialize()
ti = mt5.terminal_info(); ai = mt5.account_info()
lapor("OK" if ti and ti.trade_allowed else "BAHAYA", "Algo Trading", ti.trade_allowed if ti else "?")
lapor("OK" if ti and ti.connected else "BAHAYA", "terhubung ke broker", ti.connected if ti else "?")
lapor("OK", "akun", "%d  %s" % (ai.login, ai.server))
lapor("OK", "balance / equity", "%.2f / %.2f" % (ai.balance, ai.equity))
lapor("OK" if ai.margin_free > ai.balance * 0.8 else "PERHATIAN",
      "margin bebas", "%.2f" % ai.margin_free)
pos = mt5.positions_get() or []; orders = mt5.orders_get() or []
lapor("OK", "posisi / pending", "%d / %d" % (len(pos), len(orders)))
for p in pos:
    risiko = abs(p.price_open - p.sl) * p.volume * 100 if p.sl else 0
    lapor("PERHATIAN" if risiko > ai.balance * 0.05 else "OK",
          "  posisi %d" % p.magic, "risiko $%.2f (%.1f%% akun)" % (risiko, 100*risiko/ai.balance))

print("\n" + "=" * 92); print("D. RISIKO YANG DIIZINKAN"); print("=" * 92)
g = cfg.get("governor", {})
lapor("OK" if g.get("enabled") else "PERHATIAN", "governor", g.get("enabled"))
mr = g.get("max_risk_per_trade", 0)
# Batas per-sleeve (sl_maks_usd) kini lebih ketat dari governor, jadi governor
# berfungsi sebagai jaring TERAKHIR - bukan pengendali utama. Yang perlu diperiksa:
# apakah tiap sleeve punya batasnya sendiri, dan apakah semuanya di bawah governor.
caps = {}
for nm, sp2 in S.items():
    if sp2.get("type") == "smclimit" and sp2.get("enabled"):
        caps[nm] = float(sp2.get("params", {}).get("sl_maks_usd", 0.0))
lapor("OK", "governor max_risk (jaring akhir)", "$%d (%.0f%% akun)" % (mr, 100*mr/ai.balance))
for nm, cv in caps.items():
    ok = 0 < cv <= mr
    lapor("OK" if ok else "PERHATIAN", "  batas %s" % nm,
          ("$%.0f (%.1f%% akun)" % (cv, 100*cv/ai.balance)) if cv > 0 else "TANPA batas",
          "" if ok else "tidak ada batas per-sleeve; hanya governor yang menahan")
gj = Path(r"C:\Quant\_MONITOR\governor.json")
lapor("OK" if gj.exists() else "PERHATIAN", "governor.json",
      "ada" if gj.exists() else "TIDAK ADA", "" if gj.exists() else "signal.py fail-closed ke config")
for nm, sp in S.items():
    if sp.get("enabled") or (sp.get("type") == "orb" and sp.get("params", {}).get("pending_stop")):
        lot = sp.get("lot")
        lapor("OK" if lot == 0.01 else "PERHATIAN", "  lot %s" % nm, lot)
magics = [s["magic"] for s in cfg["live"]["strategies"]
          if s.get("enabled") or (s.get("type") == "orb" and s.get("params", {}).get("pending_stop"))]
lapor("OK" if len(set(magics)) == len(magics) else "BAHAYA", "magic unik", str(sorted(magics)))
mt5.shutdown()

print("\n" + "=" * 92); print("E. CADANGAN (GitHub)"); print("=" * 92)
os.chdir(r"C:\Quant")
lok = subprocess.run(["git","rev-parse","HEAD"],capture_output=True,text=True).stdout.strip()
try:
    rem = json.load(urllib.request.urlopen(
        "https://api.github.com/repos/PuteraKesuma/quant-/commits/master", timeout=15))["sha"]
except Exception as e:
    rem = "gagal: %s" % e
lapor("OK" if lok == rem else "BAHAYA", "SHA lokal = GitHub",
      "SAMA" if lok == rem else "BEDA", lok[:8] + " vs " + str(rem)[:8])
kotor = subprocess.run(["git","status","--porcelain"],capture_output=True,text=True).stdout.strip()
n_kotor = len([l for l in kotor.splitlines() if "health_log" not in l])
lapor("OK" if n_kotor == 0 else "PERHATIAN", "perubahan belum ter-commit", n_kotor)

print("\n" + "=" * 92); print("F. LAPIS LLM & BIAYA"); print("=" * 92)
from dotenv import load_dotenv; load_dotenv(r"C:\Quant\.env")
import anthropic
try:
    r = anthropic.Anthropic(timeout=45).messages.create(
        model="claude-sonnet-5", max_tokens=5, messages=[{"role":"user","content":"OK"}])
    lapor("OK", "kredit API", "aktif")
except Exception as e:
    lapor("PERHATIAN" if "credit" in str(e) else "BAHAYA", "kredit API", str(e)[:70],
          "agent jatuh ke angka mesin - trading TIDAK terpengaruh")
jr = Path(r"C:\Quant\smc_rr_journal.jsonl")
if jr.exists():
    rows = [json.loads(l) for l in jr.read_text(encoding="utf-8").splitlines() if l.strip()]
    tok = [r for r in rows if (r.get("token") or {}).get("in")]
    biaya = sum(r["token"]["in"]/1e6*3 + r["token"]["out"]/1e6*15 + r["token"].get("cari",0)/1000*10
                for r in tok)
    lapor("OK", "panggilan agent tercatat", "%d (dgn token: %d)" % (len(rows), len(tok)))
    lapor("OK", "biaya terpakai sejauh ini", "$%.2f" % biaya)

print("\n" + "=" * 92); print("G. DISK"); print("=" * 92)
free = ps("[math]::Round((Get-PSDrive C).Free/1GB,1)")
lapor("OK" if float(free or 0) > 5 else "BAHAYA", "ruang bebas C:", free + " GB")

print("\n" + "=" * 92)
if not TEMUAN:
    print("  VONIS: TIDAK ADA TEMUAN. Sistem aman.")
else:
    print("  TEMUAN (%d):" % len(TEMUAN))
    for t, m in TEMUAN:
        print("    [%s] %s" % (t, m))
print("=" * 92)
