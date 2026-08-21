"""Periksa apakah sistem trading benar-benar siap jalan. Jalankan setelah rebuild.

    python tools\\verify_system.py

Kenapa ini ada: dokumen pemulihan bisa usang tanpa ada yang sadar (RECOVERY.md
sempat 7 minggu ketinggalan dan masih menyuruh memasang strategi yang sudah mati).
Skrip ini tidak membaca dokumen -- dia memeriksa MESIN yang sebenarnya, jadi dia
tidak bisa usang dengan cara yang sama.

Keluaran: daftar OK / PERINGATAN / GAGAL, dan untuk tiap kegagalan, apa yang harus
dilakukan. Exit code 0 kalau tidak ada yang GAGAL.
"""
import json
import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
BRAIN_URL = "http://127.0.0.1:8000"

FAIL, WARN, OK = "GAGAL", "PERINGATAN", "OK"
results = []


def check(name, status, detail="", fix=""):
    results.append((name, status, detail, fix))


# ---------------------------------------------------------------- python + deps
def c_python():
    v = sys.version_info
    if (v.major, v.minor) < (3, 10):
        check("Python", FAIL, f"{v.major}.{v.minor}", "Install Python 3.11+")
        return
    check("Python", OK, f"{v.major}.{v.minor}.{v.micro}")

    missing = []
    for mod in ("pandas", "numpy", "yaml", "fastapi", "uvicorn", "loguru", "MetaTrader5"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        check("Dependencies", FAIL, "hilang: " + ", ".join(missing),
              "pip install -r requirements.txt")
    else:
        check("Dependencies", OK, "paket inti lengkap")


# ---------------------------------------------------------------- config
def c_config():
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    except Exception as e:
        check("config.yaml", FAIL, f"{type(e).__name__}: {e}", "Perbaiki YAML-nya")
        return None

    on = [s for s in cfg["live"]["strategies"] if s.get("enabled")]
    names = ", ".join(s["name"] for s in on) or "(tidak ada)"
    if not on:
        check("Slot brain aktif", FAIL, "tidak ada slot enabled",
              "Aktifkan minimal satu slot di config.yaml")
    else:
        check("Slot brain aktif", OK, names)

    g = cfg.get("governor") or {}
    cap, budget = g.get("max_risk_per_trade"), g.get("max_combined_risk")
    check("Risk cap per-trade", OK if cap else WARN, f"${cap}" if cap else "tidak diset",
          "" if cap else "Set governor.max_risk_per_trade")
    check("Budget risiko gabungan", OK if budget else WARN,
          f"${budget}" if budget else "tidak diset (0 = mati)",
          "" if budget else "Set governor.max_combined_risk")

    # magic governor HARUS memuat magic yang benar-benar jalan
    magics = set(g.get("magics") or [])
    live_magics = {s.get("magic") for s in on if s.get("magic")}
    live_magics.add(20250822)                      # Semi Marti (EA, bukan slot brain)
    miss = live_magics - magics
    if miss:
        check("governor.magics", FAIL, f"tidak memuat {sorted(miss)}",
              "Tambahkan magic itu ke governor.magics -- kalau tidak, governor "
              "menjaga daftar kosong")
    else:
        check("governor.magics", OK, str(sorted(magics)))
    return cfg


# ---------------------------------------------------------------- MT5
def c_mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError:
        check("MT5 python API", FAIL, "modul tidak ada", "pip install MetaTrader5")
        return None
    if not mt5.initialize():
        check("MT5 terhubung", FAIL, str(mt5.last_error()),
              "Buka MetaTrader 5 dan login ke akun")
        return None
    try:
        a = mt5.account_info()
        s = mt5.symbol_info("XAUUSD")
        check("MT5 terhubung", OK,
              f"akun {a.login} | balance ${a.balance:.2f} | leverage 1:{a.leverage}")
        if not a.trade_allowed:
            check("Algo Trading", FAIL, "trading TIDAK diizinkan di akun/terminal",
                  "Nyalakan tombol 'Algo Trading' di toolbar MT5")
        else:
            check("Algo Trading", OK, "diizinkan")
        if s is None:
            check("Simbol XAUUSD", FAIL, "tidak ditemukan",
                  "Cek nama simbol di broker (bisa XAUUSD.m dsb)")
        else:
            check("Simbol XAUUSD", OK, f"digits {s.digits} | lot min {s.volume_min}")
        return {"positions": mt5.positions_get(symbol="XAUUSD") or []}
    finally:
        mt5.shutdown()


# ---------------------------------------------------------------- file EA & preset
def c_files():
    base = Path(os.environ.get("APPDATA", "")) / "MetaQuotes" / "Terminal"
    terms = [p for p in base.glob("*") if (p / "MQL5" / "Experts").is_dir()] if base.is_dir() else []
    if not terms:
        check("Folder terminal MT5", FAIL, "tidak ditemukan",
              "Buka MT5 minimal sekali, lalu jalankan INSTALL_EA.bat")
        return

    need_ea = ["SignalExecutor.ex5", "SemiMartiV10_Gated.ex5"]
    need_set = ["SemiMartiV10_GATED.set"]
    for t in terms:
        miss_ea = [f for f in need_ea if not (t / "MQL5" / "Experts" / f).exists()]
        miss_set = [f for f in need_set if not (t / "MQL5" / "Presets" / f).exists()]
        tag = t.name[:8]
        if miss_ea:
            check(f"EA di terminal {tag}", FAIL, "hilang: " + ", ".join(miss_ea),
                  "Jalankan INSTALL_EA.bat")
        else:
            check(f"EA di terminal {tag}", OK, "SignalExecutor + SemiMartiV10_Gated")
        if miss_set:
            check(f"Preset di terminal {tag}", FAIL, "hilang: " + ", ".join(miss_set),
                  "Jalankan INSTALL_EA.bat -- TANPA preset, basket SL Semi Marti = 0")
        else:
            check(f"Preset di terminal {tag}", OK, ", ".join(need_set))

        # preset harus benar-benar memasang batas kerugian
        p = t / "MQL5" / "Presets" / "SemiMartiV10_GATED.set"
        if p.exists():
            txt = p.read_text(errors="ignore")
            sl = next((l.split("=", 1)[1].strip() for l in txt.splitlines()
                       if l.startswith("InpGlobalSL_USD=")), None)
            gate = next((l.split("=", 1)[1].strip() for l in txt.splitlines()
                         if l.startswith("InpUseRegimeGate=")), None)
            if sl in (None, "0", "0.0"):
                check("Preset: basket SL", FAIL, f"InpGlobalSL_USD={sl}",
                      "Preset ini TIDAK membatasi kerugian martingale")
            else:
                check("Preset: basket SL", OK, f"InpGlobalSL_USD={sl}")
            check("Preset: regime gate", OK if gate == "true" else WARN,
                  f"InpUseRegimeGate={gate}")


# ---------------------------------------------------------------- brain
def c_brain():
    try:
        import urllib.request
        with urllib.request.urlopen(f"{BRAIN_URL}/health", timeout=10) as r:
            h = json.loads(r.read().decode())
    except Exception as e:
        check("Brain (/health)", FAIL, f"{type(e).__name__}",
              "Jalankan START_TRADING.bat (atau biarkan watchdog yang menghidupkan)")
        return
    check("Brain (/health)", OK if h.get("status") == "ok" else WARN,
          f"status={h.get('status')} | uptime {h.get('uptime_seconds', 0) // 60} menit")

    ea = (h.get("ea") or {}).get("XAUUSD") or {}
    if ea.get("connected"):
        check("SignalExecutor polling", OK, f"terakhir {ea.get('seconds_ago')} detik lalu")
    else:
        check("SignalExecutor polling", FAIL, "EA tidak menghubungi brain",
              "Pasang SignalExecutor di chart XAUUSD dan nyalakan Algo Trading")

    risk = h.get("risk") or {}
    for w in risk.get("warnings") or []:
        check("Peringatan risiko", WARN, w)
    if (h.get("exposure") or {}).get("conflict"):
        check("Konflik posisi", FAIL, "dua slot memegang arah berlawanan",
              "Periksa /health -> exposure")


# ---------------------------------------------------------------- Semi Marti
def c_semimarti(pos_info):
    """Semi Marti TIDAK bisa diverifikasi otomatis -- katakan itu, jangan diam.

    MT5 tidak mengekspos daftar EA yang menempel di chart, baik lewat API Python
    maupun lewat file (parameter EA baru ditulis ke .chr saat profil disimpan).
    Dan karena preset mematikan InpDebug, EA ini SENYAP -- tidak adanya log bukan
    bukti dia mati, juga bukan bukti dia hidup.

    Bahayanya nyata: MT5 hanya mengizinkan SATU EA per chart. Terjadi 2026-08-21,
    SignalExecutor dan SemiMartiV10_Gated dipasang bergantian di chart XAUUSD M5
    yang sama, dan masing-masing melempar yang lain. Versi pertama skrip ini
    melaporkan "17/17 lolos" saat Semi Marti kemungkinan sudah tidak jalan.
    """
    import datetime as dt

    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return
        try:
            frm = dt.datetime.now() - dt.timedelta(days=3)
            deals = mt5.history_deals_get(frm, dt.datetime.now() + dt.timedelta(days=1)) or []
            sm = [d for d in deals if d.magic == 20250822]
            pos = [p for p in (mt5.positions_get(symbol="XAUUSD") or [])
                   if p.magic == 20250822]
        finally:
            mt5.shutdown()
    except Exception:
        return

    if pos:
        check("Semi Marti aktif", OK, f"{len(pos)} posisi terbuka sekarang")
        return
    if sm:
        last = dt.datetime.utcfromtimestamp(max(d.time for d in sm))
        check("Semi Marti aktif", WARN, f"tidak ada posisi; deal terakhir {last:%Y-%m-%d %H:%M} (server)",
              "TIDAK BISA dipastikan otomatis. Lihat pojok kanan-atas chart XAUUSD M5: "
              "harus tertulis nama EA + wajah tersenyum. MT5 hanya izinkan SATU EA per "
              "chart -- SignalExecutor dan Semi Marti WAJIB di chart terpisah.")
    else:
        check("Semi Marti aktif", WARN, "tidak ada posisi & tidak ada deal 3 hari terakhir",
              "Cek manual di chart XAUUSD M5 (nama EA + wajah tersenyum di pojok "
              "kanan-atas). Pastikan TIDAK satu chart dengan SignalExecutor.")


# ---------------------------------------------------------------- ketahanan
def c_resilience():
    startup = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / \
        "Start Menu" / "Programs" / "Startup"
    lnks = list(startup.glob("*.lnk")) if startup.is_dir() else []
    if lnks:
        check("Autostart (folder Startup)", OK, ", ".join(p.stem for p in lnks))
    else:
        check("Autostart (folder Startup)", FAIL, "tidak ada shortcut",
              "Jalankan INSTALL_AUTOSTART.bat supaya sistem hidup lagi setelah reboot")

    try:
        import subprocess
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='powershell.exe'\" | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=30).stdout
        if "watchdog" in out.lower():
            check("Watchdog", OK, "berjalan")
        else:
            check("Watchdog", WARN, "tidak terdeteksi",
                  "Jalankan _MONITOR\\watchdog_shadow.ps1 -- dia menghidupkan ulang "
                  "brain dan MT5 kalau mati")
    except Exception:
        check("Watchdog", WARN, "tidak bisa diperiksa")


if __name__ == "__main__":
    print("=" * 74)
    print("  VERIFIKASI SISTEM TRADING")
    print("=" * 74)
    c_python()
    c_config()
    pos_info = c_mt5()
    c_files()
    c_brain()
    c_semimarti(pos_info)
    c_resilience()

    print()
    width = max(len(n) for n, _, _, _ in results) + 2
    for name, status, detail, _ in results:
        mark = {OK: "  ok  ", WARN: " warn ", FAIL: " GAGAL"}[status]
        print(f"[{mark}] {name:<{width}} {detail}")

    fails = [r for r in results if r[1] == FAIL]
    warns = [r for r in results if r[1] == WARN]
    print()
    print("-" * 74)
    if fails:
        print(f"{len(fails)} GAGAL, {len(warns)} peringatan. Yang harus diperbaiki:")
        for name, _, _, fix in fails:
            if fix:
                print(f"  - {name}: {fix}")
        sys.exit(1)
    print(f"Semua pemeriksaan lolos ({len(warns)} peringatan). Sistem siap jalan.")
    for name, _, _, fix in warns:
        if fix:
            print(f"  catatan - {name}: {fix}")
    sys.exit(0)
