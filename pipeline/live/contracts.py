"""Wire format between the FastAPI signal server and the MQL5 EA.

The EA is strategy-agnostic: it just executes a list of desired-state signals.
`/signals` returns a `SignalSet` so multiple strategies/models (even on the same
symbol) can run concurrently — each `SignalResponse` carries its own `strategy`
slot and `magic`, so the EA holds an independent position per model.

Per signal: `action` is the position the EA should hold, and `signal_id` is stable
for the life of one signal, so 1-second polling never opens duplicate orders.
"""
from typing import Literal
from pydantic import BaseModel

Action = Literal["BUY", "SELL", "FLAT"]


class SignalResponse(BaseModel):
    strategy: str           # slot id (strategy/model name); tags the position
    symbol: str
    action: Action          # desired position: BUY=long, SELL=short, FLAT=none
    sl: float               # stop-loss price (0.0 when FLAT)
    tp: float               # take-profit price (0.0 when FLAT)
    lot: float              # order size
    magic: int              # per-strategy magic number (position tag in MT5)
    signal_id: str          # stable id for one signal; EA acts only when it changes
    ts: str                 # ISO-8601 UTC timestamp of evaluation


class SignalSet(BaseModel):
    symbol: str
    ts: str
    signals: list[SignalResponse]


def flat(strategy: str, symbol: str, magic: int, signal_id: str, ts: str) -> SignalResponse:
    """A 'hold no position' response for one strategy slot."""
    return SignalResponse(
        strategy=strategy, symbol=symbol, action="FLAT", sl=0.0, tp=0.0, lot=0.0,
        magic=magic, signal_id=signal_id, ts=ts,
    )
