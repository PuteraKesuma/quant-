"""Per-slot memory for the vision strategy.

One `SlotState` per magic holds three things: a cadence gate (so the expensive
Claude call runs at most once per `interval_minutes`), the signal_id lifecycle
(a counter that increments ONLY when the desired action changes, preserving EA
idempotency), and a cache of the last `SignalResponse` served between analyses.

signal_id format: ``{symbol}-{name}-VIS-{counter}`` — e.g. ``XAUUSD-vision_xau-VIS-7``.
Because the counter advances only on an action change, returning the cached
decision on every 1-second poll never produces a new signal_id, so the EA does
not re-fire orders.
"""
import pandas as pd

from ..live.contracts import SignalResponse


class SlotState:
    """Dedup + signal_id lifecycle + decision cache for one vision slot."""

    def __init__(self, symbol: str, name: str):
        self.symbol = symbol
        self.name = name
        self.prev_action: str = "FLAT"      # last committed action
        self.bars_in_state: int = 0         # analyses the slot has held prev_action
        self.last_changed: bool = False     # did the most recent commit change the action?
        self._counter: int = 0              # increments only on action change
        self._cached: SignalResponse | None = None
        self._last_attempt: pd.Timestamp | None = None

    def due(self, interval_minutes: float) -> bool:
        """True if `interval_minutes` have elapsed since the last analysis attempt
        (or none has happened yet). Side effect: when it returns True it records
        the attempt time, so a failed capture/analyze still consumes the interval
        — the Claude API is never hammered on a persistent outage."""
        now = pd.Timestamp.utcnow()
        if self._last_attempt is not None:
            if (now - self._last_attempt) < pd.Timedelta(minutes=interval_minutes):
                return False
        self._last_attempt = now
        return True

    def cached(self) -> SignalResponse | None:
        """The last decision, to serve on non-due polls. None until the first commit."""
        return self._cached

    def commit(self, action: str, builder) -> SignalResponse:
        """Apply the signal_id lifecycle for a new decision and cache it.

        action == prev_action  -> keep the counter (same signal_id; EA does nothing)
        action != prev_action  -> bump the counter (new signal_id; EA reacts)

        `builder(signal_id)` constructs the final SignalResponse. Returns it.
        """
        if action != self.prev_action:
            self._counter += 1
            self.bars_in_state = 1
            self.last_changed = True
            self.prev_action = action
        else:
            self.bars_in_state += 1
            self.last_changed = False

        signal_id = f"{self.symbol}-{self.name}-VIS-{self._counter}"
        resp = builder(signal_id)
        self._cached = resp
        return resp
