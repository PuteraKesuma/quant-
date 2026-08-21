"""Account-wide view of the book: MT5 liveness, exposure, and basket journaling.

Why this exists (2026-08-19):

1. SILENT FAILURE, OBSERVED.  The brain's MT5 handle goes stale whenever the
   MT5 terminal restarts (VPS reboot, watchdog restart, manual restart).  When
   that happened today `/signals` raised `Symbol 'XAUUSD' not found in MT5.` for
   ~2 hours while `/health` cheerfully returned `status: ok`, because health only
   reported uptime and EA polling -- it never touched MT5.  The watchdog polls
   `/health`, saw 200 OK, and never restarted anything.  eterna simply stopped
   trading and nothing noticed.  `probe_mt5()` closes that hole: health now fails
   loudly (503) so the watchdog's existing 3-strikes rule restarts the brain.

2. THE BRAIN IS BLIND TO THE EA IT SHARES A SYMBOL WITH.  `Semi Marti Cuan v10`
   (magic 20250822) trades XAUUSD autonomously -- it never polls the brain.  It
   is a FADE system; eterna (920627) is a TREND FOLLOWER, on the same symbol.
   They will take opposing positions.  The brain could not previously see this
   because it only tracked its own slots.  `exposure()` reports every magic on
   the account so the conflict is visible instead of invisible.

3. WE NEED REAL BASKET DATA, NOT AN OVERFIT.  The loss-clustering study of the
   2026 tester run found only 33 baskets / 8 losers -- far too thin to fit a
   regime filter on without curve-fitting (the one apparent signal, 06-11 UTC,
   rests on 8 observations and is worth ~+$86 on +$960).  `BasketTracker` records
   every live Semi Marti basket -- open time, peak legs, peak floating loss,
   close time, realised P&L -- so that in a few months the question can be
   answered from a real sample instead of a guess.

Nothing here places, modifies, or blocks an order.  It observes and reports.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from loguru import logger

# magic -> human label.  Anything not listed shows up as "unknown:<magic>",
# which is itself useful: it means something is trading that we did not expect.
KNOWN_MAGICS: dict[int, str] = {
    920627: "eterna_xau",       # brain-managed, trend follower
    20250822: "semi_marti",     # autonomous EA, fade + martingale basket
}
SEMI_MARTI_MAGIC = 20250822

_JOURNAL = Path(r"C:\Quant\_MONITOR\basket_journal.jsonl")


def _mt5():
    import MetaTrader5 as mt5
    return mt5


def probe_mt5(symbol: str = "XAUUSD") -> dict:
    """Cheap liveness probe: can we still reach MT5 and see the symbol?

    Returns {"ok": bool, "detail": str}.  Never raises -- a probe that throws
    would defeat its own purpose.
    """
    try:
        mt5 = _mt5()
        info = mt5.symbol_info(symbol)
        if info is None:
            return {"ok": False, "detail": f"symbol_info({symbol}) is None "
                                           "(stale MT5 handle or symbol hidden)"}
        tick = mt5.symbol_info_tick(symbol)
        if tick is None or not tick.bid:
            return {"ok": False, "detail": f"no tick for {symbol}"}
        return {"ok": True, "detail": f"bid={tick.bid}"}
    except Exception as e:                       # noqa: BLE001 - probe must not raise
        return {"ok": False, "detail": f"{type(e).__name__}: {e}"}


def positions_by_magic() -> dict:
    """Every open position on the account, grouped by magic. Never raises."""
    out: dict[str, dict] = {}
    try:
        mt5 = _mt5()
        for p in (mt5.positions_get() or []):
            label = KNOWN_MAGICS.get(p.magic, f"unknown:{p.magic}")
            e = out.setdefault(label, {"magic": p.magic, "symbol": p.symbol,
                                       "legs": 0, "buy_lots": 0.0, "sell_lots": 0.0,
                                       "floating": 0.0})
            e["legs"] += 1
            if p.type == 0:
                e["buy_lots"] += p.volume
            else:
                e["sell_lots"] += p.volume
            e["floating"] += p.profit
        for e in out.values():
            e["buy_lots"] = round(e["buy_lots"], 3)
            e["sell_lots"] = round(e["sell_lots"], 3)
            e["net_lots"] = round(e["buy_lots"] - e["sell_lots"], 3)
            e["floating"] = round(e["floating"], 2)
    except Exception:                            # noqa: BLE001
        logger.exception("positions_by_magic failed")
    return out


def exposure(symbol: str = "XAUUSD") -> dict:
    """Combined view for one symbol: who holds what, and are they fighting?

    `conflict` is True when two different magics hold opposite directions on the
    same symbol -- eterna long while Semi Marti is short, for example.  That is
    not automatically wrong (they are different edges) but it means paying spread
    on both sides for partially self-cancelling exposure, and it is precisely the
    situation nobody could see before.
    """
    by = positions_by_magic()
    here = {k: v for k, v in by.items() if v["symbol"] == symbol}
    longs = [k for k, v in here.items() if v["net_lots"] > 0]
    shorts = [k for k, v in here.items() if v["net_lots"] < 0]
    return {
        "symbol": symbol,
        "holders": here,
        "net_lots": round(sum(v["net_lots"] for v in here.values()), 3),
        "gross_lots": round(sum(v["buy_lots"] + v["sell_lots"] for v in here.values()), 3),
        "floating": round(sum(v["floating"] for v in here.values()), 2),
        "conflict": bool(longs and shorts),
        "long_by": longs,
        "short_by": shorts,
    }


def risk_snapshot(symbol: str = "XAUUSD") -> dict:
    """Account-level risk the individual strategies cannot see.

    eterna asks the brain before entering, so it is governed. Semi Marti is an
    autonomous EA that never asks -- and its stop is VIRTUAL: there is no broker
    SL on its legs (`sl=0.0`), the EA itself closes the basket at -$75 floating.
    Nothing in the system previously combined the two exposures, so a Semi Marti
    basket sitting near its stop while eterna opens a position that also moves
    against could take the account deeper than either strategy's own numbers
    imply.

    This REPORTS, it does not block. Blocking would necessarily fall on eterna
    (the only one that asks), i.e. it would sacrifice the validated edge -- whose
    profit is extremely concentrated (10 best trades = 85% of all profit) --
    to protect the unvalidated one. Wrong way round. The regime gate handles
    Semi Marti on the EA side instead.

    All money maths uses the broker's own contract size and point, read live from
    MT5 rather than hardcoded: FBS XAUUSD is digits=2, point=0.01,
    contract=100oz, so at 0.01 lot a $1 price move is $1 -- but that ratio is a
    property of this symbol on this broker, not a constant.
    """
    out = {"symbol": symbol, "warnings": []}
    try:
        mt5 = _mt5()
        acc = mt5.account_info()
        info = mt5.symbol_info(symbol)
        if acc is None or info is None:
            return {**out, "error": "account/symbol unavailable"}

        equity = float(acc.equity)
        contract = float(info.trade_contract_size)
        positions = [p for p in (mt5.positions_get(symbol=symbol) or [])]

        floating = round(sum(p.profit for p in positions), 2)
        gross_lots = round(sum(p.volume for p in positions), 3)

        # Worst case still ahead of us, per magic:
        #   - a leg WITH a broker stop can only lose to that stop
        #   - a leg WITHOUT one (Semi Marti) is bounded by the EA's basket stop,
        #     which only holds while the EA is alive -- counted, but flagged
        open_risk = 0.0
        unprotected_lots = 0.0
        for p in positions:
            if p.sl:
                open_risk += abs(p.price_open - p.sl) * p.volume * contract
            else:
                unprotected_lots += p.volume
        if unprotected_lots:
            # Semi Marti's virtual basket stop is the real bound for those legs
            open_risk += SEMI_MARTI_BASKET_SL_USD

        pct = lambda v: round(v / equity * 100, 2) if equity else 0.0
        out.update({
            "equity": round(equity, 2),
            "floating": floating,
            "floating_pct": pct(floating),
            "gross_lots": gross_lots,
            "worst_case_loss": round(open_risk, 2),
            "worst_case_pct": pct(open_risk),
            "unprotected_lots": round(unprotected_lots, 3),
            "contract_size": contract,
        })

        if floating < 0 and abs(floating) >= equity * 0.05:
            out["warnings"].append(
                f"floating loss {floating:.2f} is {abs(pct(floating)):.1f}% of equity")
        if open_risk >= equity * 0.20:
            out["warnings"].append(
                f"worst-case open risk {open_risk:.2f} is {pct(open_risk):.1f}% of equity")
        if unprotected_lots:
            out["warnings"].append(
                f"{unprotected_lots:.2f} lots have NO broker stop (Semi Marti basket "
                f"stop is enforced by the EA, not the server)")
        return out
    except Exception as e:                       # noqa: BLE001
        logger.exception("risk_snapshot failed")
        return {**out, "error": f"{type(e).__name__}: {e}"}


SEMI_MARTI_BASKET_SL_USD = 75.0   # InpGlobalSL_USD in the running preset


def committed_risk(symbol: str) -> float:
    """Dollars already at risk in OPEN positions on `symbol`, across every book.

    Same accounting as risk_snapshot()["worst_case_loss"], pulled out so the
    ENTRY path can consult it: a leg with a broker stop can only lose to that
    stop; Semi Marti's legs carry no broker stop and are bounded by the EA's
    virtual basket stop instead.

    WHY THIS EXISTS
    eterna is capped at $70 per trade and Semi Marti's basket at $75, so with
    both open the account is exposed to $145 -- 27% of a $538 equity. Measured
    on 2026 that state only exists 0.8% of the time, so clamping eterna's stop
    on every trade to defend against it costs about $190 a year to fix
    something that is almost never on. Charging the ENTRY against a combined
    budget instead only bites when the other book is actually open: 3 of 42
    eterna entries in 2026.

    Fails to 0.0 rather than raising -- a risk check that throws must never be
    what stops the brain from trading.
    """
    try:
        import MetaTrader5 as mt5

        positions = mt5.positions_get(symbol=symbol)
        if not positions:
            return 0.0
        info = mt5.symbol_info(symbol)
        contract = float(info.trade_contract_size) if info else 100.0

        total = 0.0
        unprotected = False
        for p in positions:
            if p.sl:
                total += abs(p.price_open - p.sl) * p.volume * contract
            else:
                unprotected = True
        if unprotected:
            total += SEMI_MARTI_BASKET_SL_USD
        return round(total, 2)
    except Exception:                                # noqa: BLE001
        logger.exception("committed_risk failed; treating as 0")
        return 0.0


class BasketTracker:
    """Journals Semi Marti basket lifecycle from position snapshots.

    A 'basket' is the run from the first leg opening until the account is flat on
    that magic again (the EA closes the whole basket at once on global TP/SL).
    We poll rather than hook because the EA is third-party and unmodified.

    Records, per basket: open/close time, peak leg count, peak floating loss
    (how deep the martingale went), and realised P&L inferred from the last
    floating value before it went flat.  The peak-legs and peak-loss figures are
    the interesting ones -- they say how close a winning basket came to the SL.
    """

    def __init__(self, magic: int = SEMI_MARTI_MAGIC, journal: Path = _JOURNAL):
        self.magic = magic
        self.journal = journal
        self.open_since: float | None = None
        self.peak_legs = 0
        self.peak_loss = 0.0
        self.last_floating = 0.0
        self.last_legs = 0

    def poll(self) -> None:
        """Call periodically (heartbeat cadence is fine). Never raises."""
        try:
            mt5 = _mt5()
            mine = [p for p in (mt5.positions_get() or []) if p.magic == self.magic]
            legs = len(mine)
            floating = round(sum(p.profit for p in mine), 2)

            if legs and self.open_since is None:                  # basket opened
                self.open_since = time.time()
                self.peak_legs, self.peak_loss = legs, min(0.0, floating)
                self._write({"event": "open", "legs": legs,
                             "symbol": mine[0].symbol,
                             "side": "BUY" if mine[0].type == 0 else "SELL"})
            elif legs and self.open_since is not None:            # basket running
                self.peak_legs = max(self.peak_legs, legs)
                self.peak_loss = min(self.peak_loss, floating)
            elif not legs and self.open_since is not None:        # basket closed
                self._write({"event": "close",
                             "held_minutes": round((time.time() - self.open_since) / 60, 1),
                             "peak_legs": self.peak_legs,
                             "peak_floating_loss": self.peak_loss,
                             "last_floating_before_close": self.last_floating,
                             "last_legs_before_close": self.last_legs})
                self.open_since = None
                self.peak_legs, self.peak_loss = 0, 0.0

            if legs:
                self.last_floating, self.last_legs = floating, legs
        except Exception:                                          # noqa: BLE001
            logger.exception("BasketTracker.poll failed")

    def _write(self, rec: dict) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "magic": self.magic, **rec}
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:                                          # noqa: BLE001
            logger.exception("basket journal write failed")
        logger.info(f"[basket] {rec['event']} {rec}")

    def state(self) -> dict:
        return {
            "open": self.open_since is not None,
            "open_minutes": (round((time.time() - self.open_since) / 60, 1)
                             if self.open_since else 0),
            "legs": self.last_legs if self.open_since else 0,
            "peak_legs": self.peak_legs,
            "peak_floating_loss": self.peak_loss,
        }
