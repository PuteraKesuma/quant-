"""Heartbeat watcher — one-screen status of the live signal server.

Run in its own terminal to see, at a glance, whether the server is alive, which
model is active, whether the EA is still polling, and the current action:

    python -m pipeline.live.heartbeat            # refresh every 5s
    python -m pipeline.live.heartbeat --once     # print once and exit
    python -m pipeline.live.heartbeat --interval 2
"""
import argparse
import time
from datetime import datetime

import requests

from ..fetch.base_fetcher import load_config


def _base_url(cfg: dict) -> str:
    lv = cfg["live"]
    return f"http://{lv['host']}:{lv['port']}"


def poll_once(base: str) -> str:
    stamp = datetime.now().strftime("%H:%M:%S")
    try:
        h = requests.get(f"{base}/health", timeout=2).json()
    except Exception as e:
        return f"[{stamp}] server=DOWN  ({type(e).__name__})  -> is it running? python -m pipeline.live.run_server"

    models = ", ".join(f"{s['name']}({s['type']}->{s['symbol']})" for s in h["strategies"])
    ea = h.get("ea") or {}
    if ea:
        ea_txt = " ".join(
            f"{sym}={'UP' if v['connected'] else 'STALE'}({v['seconds_ago']}s)"
            for sym, v in ea.items()
        )
    else:
        ea_txt = "EA not seen yet (attach EA + enable Algo Trading)"

    # current action per distinct symbol
    actions = []
    for sym in {s["symbol"] for s in h["strategies"]}:
        try:
            r = requests.get(f"{base}/signals", params={"symbol": sym}, timeout=3).json()
            for sig in r["signals"]:
                tag = sig["signal_id"].rsplit("-", 1)[-1]
                actions.append(f"{sig['strategy']}:{sig['action']}/{tag}")
        except Exception:
            actions.append(f"{sym}:?")

    return (f"[{stamp}] server=UP up={h['uptime_seconds']}s | model={models} | "
            f"EA {ea_txt} | {' '.join(actions)}")


def main():
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--url", default=_base_url(cfg))
    args = ap.parse_args()

    if args.once:
        print(poll_once(args.url))
        return
    print(f"Watching {args.url}  (Ctrl-C to stop)")
    try:
        while True:
            print(poll_once(args.url))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("stopped.")


if __name__ == "__main__":
    main()
