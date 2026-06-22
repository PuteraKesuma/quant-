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
from loguru import logger

from ..fetch.base_fetcher import load_config
from .contracts import SignalSet
from .signal import SignalEngine

_cfg = load_config()
_engine = SignalEngine(_cfg)
_strategies = _cfg["live"]["strategies"]
_default_symbol = _strategies[0]["symbol"] if _strategies else None
_hb_seconds = _cfg["live"].get("heartbeat_seconds", 15)
_ea_timeout = max(3.0, 3 * _cfg["live"].get("poll_seconds", 1))  # EA "connected" if seen within this

_start = time.time()
_last_poll: dict[str, float] = {}   # symbol -> monotonic time of last EA poll


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
def health() -> dict:
    return {
        "status": "ok",
        "now_utc": pd.Timestamp.utcnow().isoformat(),
        "uptime_seconds": int(time.time() - _start),
        "strategies": [{"name": s["name"], "type": s["type"], "symbol": s["symbol"]}
                       for s in _strategies],
        "ea": _ea_status(),
    }


@app.get("/signals", response_model=SignalSet)
def signals(symbol: str = None) -> SignalSet:
    symbol = symbol or _default_symbol
    if symbol not in _cfg["symbols"]:
        raise HTTPException(status_code=404, detail=f"Unknown symbol: {symbol}")
    _last_poll[symbol] = time.time()   # record EA liveness
    try:
        sigs = _engine.evaluate(symbol)
        return SignalSet(symbol=symbol, ts=pd.Timestamp.utcnow().isoformat(), signals=sigs)
    except Exception as e:
        logger.exception(f"[{symbol}] signal evaluation failed")
        raise HTTPException(status_code=500, detail=str(e))
