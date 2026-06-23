"""Pre-flight check for the live trading brain.

Run before starting the signal server so the user gets a clear READY / NOT READY
verdict instead of a stack trace. Exits 0 if everything looks good, 1 otherwise.

Checks: .env API key (only required if a vision slot is configured), required
deps importable, config loads with at least one live slot, MT5 terminal reachable.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OK = "[ OK ]"
BAD = "[FAIL]"
WARN = "[WARN]"


def main() -> int:
    problems: list[str] = []
    warns: list[str] = []
    print("=" * 60)
    print(" PRE-FLIGHT CHECK - ORB Trading Brain")
    print("=" * 60)

    # --- config ---
    try:
        import yaml
        cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
        slots = cfg.get("live", {}).get("strategies", [])
        if not slots:
            problems.append("config.yaml: tidak ada slot di live.strategies")
        else:
            print(f"{OK} config.yaml ke-load, {len(slots)} slot:")
            for s in slots:
                print(f"        - {s.get('name'):14} type={s.get('type'):7} "
                      f"symbol={s.get('symbol'):8} magic={s.get('magic')}")
    except Exception as e:  # noqa: BLE001
        cfg, slots = {}, []
        problems.append(f"config.yaml gagal di-load: {e}")
        print(f"{BAD} config.yaml gagal di-load: {e}")

    has_vision = any(s.get("type") == "vision" for s in slots)

    # --- API key (only needed for vision) ---
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        import os
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        placeholder = (not key) or key.startswith("sk-ant-xxxx")
        if has_vision and placeholder:
            problems.append(".env: ANTHROPIC_API_KEY belum diisi (dibutuhkan slot vision)")
            print(f"{BAD} ANTHROPIC_API_KEY belum diisi - slot vision butuh ini")
        elif has_vision:
            print(f"{OK} ANTHROPIC_API_KEY terisi ({key[:14]}...{key[-4:]})")
        else:
            print(f"{OK} Tidak ada slot vision - API key tidak wajib")
    except Exception as e:  # noqa: BLE001
        warns.append(f"cek .env dilewati: {e}")

    # --- deps ---
    import importlib
    need = ["fastapi", "uvicorn", "MetaTrader5", "pandas", "yaml", "loguru"]
    if has_vision:
        need += ["anthropic", "mplfinance"]
    for mod in need:
        try:
            importlib.import_module(mod)
            print(f"{OK} dep {mod}")
        except Exception as e:  # noqa: BLE001
            problems.append(f"dep '{mod}' tidak ter-install: {e}")
            print(f"{BAD} dep {mod} TIDAK ada - jalankan: pip install -r requirements.txt")

    # --- MT5 terminal reachable ---
    try:
        import MetaTrader5 as mt5
        if mt5.initialize():
            ti = mt5.terminal_info()
            acc = mt5.account_info()
            who = f"login={acc.login}" if acc else "belum login"
            algo = getattr(ti, "trade_allowed", None)
            print(f"{OK} MT5 terhubung ({who}, AlgoTrading={'ON' if algo else 'OFF?'})")
            if algo is False:
                warns.append("MT5 AlgoTrading kelihatannya OFF - nyalakan tombol Algo Trading")
            mt5.shutdown()
        else:
            warns.append(f"MT5 belum bisa di-connect: {mt5.last_error()} "
                         "- buka terminal MT5 & login dulu")
            print(f"{WARN} MT5 belum konek: {mt5.last_error()}")
    except Exception as e:  # noqa: BLE001
        warns.append(f"cek MT5 dilewati: {e}")

    # --- verdict ---
    print("=" * 60)
    for w in warns:
        print(f"{WARN} {w}")
    if problems:
        print(f"\n NOT READY - {len(problems)} masalah harus dibereskan:")
        for p in problems:
            print(f"   x {p}")
        print("=" * 60)
        return 1
    print("\n READY - semua cek lolos. Brain siap dijalankan.")
    if warns:
        print(" (ada warning di atas - server tetap bisa jalan, tapi cek dulu)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
