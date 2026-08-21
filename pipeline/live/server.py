"""FastAPI signal server — the strategy-agnostic 'brain' the MQL5 EA polls.

  GET /health               -> heartbeat: status, uptime, active models, EA liveness
  GET /signals?symbol=NAS100 -> SignalSet {symbol, ts, signals:[SignalResponse...]}

Returns a *list* so multiple strategies/models can run concurrently per symbol.
Also emits a heartbeat log line every `live.heartbeat_seconds` so the terminal
shows the server is alive and whether the EA is still polling.
"""
import asyncio
import time
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

from ..fetch.base_fetcher import load_config
from .book import BasketTracker, exposure, probe_mt5, risk_snapshot
from .contracts import SignalSet
from .signal import SignalEngine

_cfg = load_config()
_engine = SignalEngine(_cfg)
_strategies = [s for s in _cfg["live"]["strategies"] if s.get("enabled", True)]
_default_symbol = _strategies[0]["symbol"] if _strategies else None
_hb_seconds = _cfg["live"].get("heartbeat_seconds", 15)
_ea_timeout = max(3.0, 3 * _cfg["live"].get("poll_seconds", 1))  # EA "connected" if seen within this

_start = time.time()
_last_poll: dict[str, float] = {}   # symbol -> monotonic time of last EA poll
_basket = BasketTracker()           # journals the autonomous Semi Marti EA's baskets
_MT5_GRACE_SECONDS = 90             # boot window before a failed MT5 probe means 503
_last_risk_key: tuple = ()          # dedupe repeated risk warnings in the heartbeat


def _ea_status() -> dict:
    now = time.time()
    out = {}
    for sym, t in _last_poll.items():
        ago = now - t
        out[sym] = {"seconds_ago": round(ago, 1), "connected": ago <= _ea_timeout}
    return out


async def _heartbeat_loop():
    slots = ", ".join(f"{s['name']}({s['type']}->{s['symbol']})" for s in _strategies)
    while True:
        await asyncio.sleep(_hb_seconds)
        _basket.poll()          # detect Semi Marti basket open/close -> journal
        # Surface account-level risk in the terminal/log. Warnings that persist
        # are logged once per state change, not every heartbeat, so a basket that
        # sits open for an hour does not bury the log.
        try:
            rs = risk_snapshot(_default_symbol or "XAUUSD")
            key = tuple(rs.get("warnings", []))
            global _last_risk_key
            if key and key != _last_risk_key:
                for w in rs["warnings"]:
                    logger.warning(f"[risk] {w}  (equity {rs.get('equity')}, "
                                   f"floating {rs.get('floating')})")
            _last_risk_key = key
        except Exception:       # noqa: BLE001 - never let reporting break the loop
            logger.exception("risk heartbeat failed")
        ea = _ea_status()
        if not ea:
            ea_txt = "EA not seen yet"
        else:
            ea_txt = " ".join(
                f"{sym}={'UP' if v['connected'] else 'STALE'}({v['seconds_ago']}s)"
                for sym, v in ea.items()
            )
        logger.info(f"HEARTBEAT | up={int(time.time()-_start)}s | slots: {slots} | {ea_txt}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(_heartbeat_loop())
    logger.info(f"Heartbeat every {_hb_seconds}s; EA timeout {_ea_timeout}s")
    yield
    task.cancel()


app = FastAPI(title="Signal Server", version="2.1", lifespan=lifespan)


@app.get("/health")
def health():
    """Deep health check.

    Returns 503 when MT5 is unreachable so the watchdog actually restarts the
    brain.  Before 2026-08-19 this endpoint reported only uptime and EA polling,
    so when the MT5 handle went stale after a terminal restart it kept answering
    200 OK for hours while every /signals call raised
    `Symbol 'XAUUSD' not found in MT5.` -- eterna silently stopped trading and
    the watchdog never noticed.  A stale handle is only recoverable by restarting
    the brain, which is exactly what a failing health check triggers.
    """
    up = int(time.time() - _start)
    mt5_probe = probe_mt5(_default_symbol or "XAUUSD")
    # Startup grace: the MT5 python binding needs a moment to attach in a fresh
    # process, and a health check observed it failing at uptime 0s on a perfectly
    # healthy boot.  Preflight already proved MT5 was reachable seconds earlier,
    # so a failure inside the grace window is boot lag, not an outage -- reporting
    # 503 there would have the watchdog kill a brain that is starting normally.
    starting = (not mt5_probe["ok"]) and up < _MT5_GRACE_SECONDS
    body = {
        "status": "ok" if mt5_probe["ok"] else ("starting" if starting else "degraded"),
        "now_utc": pd.Timestamp.utcnow().isoformat(),
        "uptime_seconds": int(time.time() - _start),
        "strategies": [{"name": s["name"], "type": s["type"], "symbol": s["symbol"]}
                       for s in _strategies],
        "ea": _ea_status(),
        "mt5": mt5_probe,
        "exposure": exposure(_default_symbol or "XAUUSD"),
        "risk": risk_snapshot(_default_symbol or "XAUUSD"),
        "semi_marti": _basket.state(),
    }
    if not mt5_probe["ok"]:
        if starting:
            logger.warning(f"health STARTING ({up}s): MT5 not attached yet -- "
                           f"{mt5_probe['detail']}")
            return body                      # 200: do not trip the watchdog on boot
        logger.error(f"health DEGRADED: MT5 probe failed -- {mt5_probe['detail']}")
        return JSONResponse(status_code=503, content=body)
    return body


@app.get("/signals", response_model=SignalSet)
def signals(symbol: str = None) -> SignalSet:
    symbol = symbol or _default_symbol
    if symbol not in _cfg["symbols"]:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
    _last_poll[symbol] = time.time()   # record EA liveness
    try:
        # Slots that have not yet read the broker's book are withheld by the
        # engine (see SignalEngine.evaluate), so a startup race can never be
        # delivered to the EA as "close your position".
        sigs = _engine.evaluate(symbol)
        return SignalSet(symbol=symbol, ts=pd.Timestamp.utcnow().isoformat(), signals=sigs)
    except Exception as e:
        logger.exception(f"[{symbol}] signal evaluation failed")
        raise HTTPException(status_code=500, detail=str(e))
