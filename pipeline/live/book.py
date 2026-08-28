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

Everything here observes and reports, with ONE deliberate exception added
2026-08-26: `BasketGuardian`, which can close a Semi Marti basket.  See its
docstring for why that exception exists and how it avoids fighting the EA.
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


_MT5_READY = False


def _mt5():
    """MT5, guaranteed attached to the LIVE terminal.

    This used to only import the module and rely on someone else having called
    initialize() -- in practice DataProvider, the first time a strategy asked for
    bars. That dependency broke the moment the last strategy slot was disabled on
    2026-08-28: with no slot, nothing fetched bars, nothing initialised MT5, and
    every call here returned None. /health went 503, the watchdog restarted the
    brain, and it happened again -- a restart loop. BasketGuardian lives in this
    module and enforces Semi Marti's only real basket stop, so a blind book module
    is a safety failure, not an inconvenience.

    The path is explicit for a second reason found the same day: this machine runs
    a portable terminal in mt5_tester/ for backtests, and a bare initialize()
    attaches to whichever it likes. Attaching to the tester makes symbol_info
    return None. If the live terminal is not the one we get, fail loudly rather
    than operate through the wrong terminal.
    """
    global _MT5_READY
    import MetaTrader5 as mt5
    if not _MT5_READY:
        try:
            from ..fetch.base_fetcher import load_config
            path = ((load_config().get("live") or {}).get("mt5_terminal_path")
                    or r"C:\Program Files\MetaTrader 5\terminal64.exe")
        except Exception:                                    # noqa: BLE001
            path = r"C:\Program Files\MetaTrader 5\terminal64.exe"
        if mt5.terminal_info() is None and not mt5.initialize(path=path):
            raise RuntimeError(f"MT5 initialize(path={path!r}) failed: "
                               f"{mt5.last_error()}")
        ti = mt5.terminal_info()
        if ti is not None and ti.path and "mt5_tester" in ti.path.replace("/", "\\"):
            raise RuntimeError(f"MT5 attached to the BACKTEST terminal ({ti.path}); "
                               f"refusing. Expected {path}")
        _MT5_READY = True
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
        self.opens: list[float] = []        # waktu basket dibuka, 24 jam terakhir

    def poll(self) -> None:
        """Call periodically (heartbeat cadence is fine). Never raises."""
        try:
            mt5 = _mt5()
            mine = [p for p in (mt5.positions_get() or []) if p.magic == self.magic]
            legs = len(mine)
            floating = round(sum(p.profit for p in mine), 2)

            if legs and self.open_since is None:                  # basket opened
                self.open_since = time.time()
                self.opens.append(self.open_since)
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

    def opens_24h(self) -> int:
        cut = time.time() - 86400
        self.opens = [t for t in self.opens if t >= cut]
        return len(self.opens)

    def state(self) -> dict:
        n = self.opens_24h()
        return {
            "open": self.open_since is not None,
            "open_minutes": (round((time.time() - self.open_since) / 60, 1)
                             if self.open_since else 0),
            "legs": self.last_legs if self.open_since else 0,
            "peak_legs": self.peak_legs,
            "peak_floating_loss": self.peak_loss,
            "opens_24h": n,
            "rate_alarm": n > BASKET_RATE_ALARM,
        }


# Basket per 24 jam yang, kalau dilampaui, berarti ada rem yang lepas.
#
# KENAPA ANGKA INI ADA
# Setelan live memakai InpRequireBreakConfirm=false, yang membuka posisi pada
# sinyal MENTAH -- tiap tick, tanpa menunggu konfirmasi. Yang menahannya cuma
# filter berita EA. Diukur di akun: 1,2 basket/hari. Diukur di Strategy Tester,
# yang TIDAK punya kalender berita sehingga filternya mati: 28,3 basket/hari, dan
# run tick-asli 8 minggu pada laju itu berakhir dengan akun habis (-$500.82,
# PF 0.93, DD 100.4%).
#
# Jadi kalau kalender MT5 suatu hari tidak termuat -- dan itu gagal DIAM-DIAM --
# EA melompat ke laju yang terbukti menghancurkan, tanpa satu pun pesan error.
# Ambang 6 memberi jarak lebar dari laju normal (1-2) sekaligus jauh di bawah
# laju berbahaya (28), jadi alarm palsu tidak mungkin dan alarm asli pasti kena.
#
# Ini PERINGATAN, bukan pemutus. Yang memutus tetap SL basket $75 di EA dan
# BasketGuardian di -$110.
BASKET_RATE_ALARM = 6


# Backstop level. The EA's own basket stop is SEMI_MARTI_BASKET_SL_USD ($75); the
# gap between them is deliberate headroom -- see BasketGuardian.
GUARD_HARD_STOP_USD = 110.0
GUARD_CONFIRM_POLLS = 3          # consecutive breaches before acting
GUARD_COOLDOWN_S = 300           # do not fire twice in a row on the same event


class BasketGuardian:
    """Last-resort basket stop for Semi Marti, enforced from Python.

    WHY THIS BREAKS THE "OBSERVE ONLY" RULE

    Semi Marti's $75 basket stop is VIRTUAL. Its legs sit at the broker with
    sl=0.00 and tp=0.00 -- go and look, the risk snapshot has warned about this
    for days. The only thing that closes a losing basket is the EA itself,
    running, on a chart, every tick.

    So the EA is a single point of failure for the ONLY stop that exists. If it
    is removed from the chart, if MT5 restarts without it, if the chart profile
    loses it after a reboot (that happened 2026-07-05 and went unnoticed for 2.5
    days), then the basket has no stop at all and can run to any depth. On
    2026-08-26 the EA was re-initialised three times in one hour by ordinary
    actions -- attaching, changing timeframe, closing the F7 dialog -- and each
    re-init silently discarded state. Nothing about that arrangement is safe
    enough to be the only line of defence on real money.

    The brain is a separate process with its own MT5 connection. It survives EA
    reloads, chart changes and terminal restarts. That makes it the right place
    for a backstop.

    HOW IT AVOIDS FIGHTING THE EA

    It does not duplicate the EA's job. It triggers only where a WORKING EA
    would already have acted long ago:

      EA closes the basket at        -$75
      largest real loss ever seen    -$75.34 live, -$74.49 in the tester
      this guardian closes at       -$110

    Reaching -$110 means the EA did not act at -$75, so it is not running or not
    functioning. The $35 gap absorbs the slippage and gaps that can legitimately
    carry a close past -$75 while the EA IS working, so in normal operation this
    code never fires. If it ever does fire, that is itself the alarm.

    Two more guards against acting on noise: the breach must hold for
    GUARD_CONFIRM_POLLS consecutive polls (a single bad tick is not enough), and
    a cooldown stops it firing repeatedly on one event.

    RETCODES ARE NOT TRUSTED. This broker returns retcode 0 on every single
    order -- opens and closes alike -- while actually executing them. That cost
    the EA its Dual Entry for weeks (see SemiMartiV10_Gated.mq5,
    OpenOrderReturnTicket). So success here is decided by re-reading the book and
    checking the position is gone, never by the return code.
    """

    def __init__(self, magic: int = SEMI_MARTI_MAGIC,
                 hard_stop: float = GUARD_HARD_STOP_USD,
                 confirm_polls: int = GUARD_CONFIRM_POLLS,
                 cooldown_s: float = GUARD_COOLDOWN_S,
                 journal: Path = _JOURNAL):
        self.magic = magic
        self.hard_stop = abs(hard_stop)
        self.confirm_polls = max(1, confirm_polls)
        self.cooldown_s = cooldown_s
        self.journal = journal
        self.breaches = 0
        self.last_fired: float | None = None
        self.fired_count = 0
        self.last_floating = 0.0
        self.last_legs = 0

    # ------------------------------------------------------------------ poll
    def poll(self) -> dict:
        """Call every heartbeat. Never raises -- a guardian that crashes is worse
        than no guardian, because it looks present while doing nothing."""
        try:
            mt5 = _mt5()
            mine = [p for p in (mt5.positions_get() or [])
                    if p.magic == self.magic]
            # profit + swap, matching how the EA measures its own basket
            floating = round(sum(p.profit + p.swap for p in mine), 2)
            self.last_floating, self.last_legs = floating, len(mine)

            if not mine or floating > -self.hard_stop:
                self.breaches = 0
                return self.state()

            self.breaches += 1
            logger.warning(
                f"[guardian] basket {floating:.2f} <= -{self.hard_stop:.0f} "
                f"({self.breaches}/{self.confirm_polls}) -- the EA should have "
                f"closed this at -{SEMI_MARTI_BASKET_SL_USD:.0f}")
            if self.breaches < self.confirm_polls:
                return self.state()

            if self.last_fired and (time.time() - self.last_fired) < self.cooldown_s:
                return self.state()

            self._close_all(mine, floating)
            return self.state()
        except Exception:                                          # noqa: BLE001
            logger.exception("BasketGuardian.poll failed")
            return self.state()

    # ------------------------------------------------------------- execution
    def _close_all(self, positions, floating: float) -> None:
        mt5 = _mt5()
        self.last_fired = time.time()
        self.fired_count += 1
        logger.error(
            f"[guardian] FIRING: closing {len(positions)} Semi Marti legs at "
            f"{floating:.2f}. The EA did not enforce its own -"
            f"{SEMI_MARTI_BASKET_SL_USD:.0f} stop.")

        closed, failed = [], []
        for p in positions:
            if self._close_one(mt5, p):
                closed.append(p.ticket)
            else:
                failed.append(p.ticket)

        self._write({
            "event": "guardian_fired",
            "floating": floating,
            "hard_stop": -self.hard_stop,
            "legs": len(positions),
            "closed": closed,
            "failed": failed,
        })
        if failed:
            logger.error(f"[guardian] STILL OPEN after close attempt: {failed} "
                         f"-- manual intervention needed")

    def _close_one(self, mt5, p) -> bool:
        """Close one leg. Verifies by re-reading the book, not by retcode."""
        opposite = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY \
            else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(p.symbol)
        if tick is None:
            logger.error(f"[guardian] no tick for {p.symbol}; cannot close #{p.ticket}")
            return False
        price = tick.bid if opposite == mt5.ORDER_TYPE_SELL else tick.ask

        req = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": p.ticket,
            "symbol": p.symbol,
            "volume": p.volume,
            "type": opposite,
            "price": price,
            "deviation": 50,
            "magic": self.magic,
            "comment": "guardian",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        res = mt5.order_send(req)
        rc = getattr(res, "retcode", None) if res else None

        # Never trust rc: this broker returns 0 on success. Re-read the book.
        for _ in range(10):
            if not any(q.ticket == p.ticket
                       for q in (mt5.positions_get(symbol=p.symbol) or [])):
                logger.info(f"[guardian] closed #{p.ticket} (rc={rc})")
                return True
            time.sleep(0.05)
        logger.error(f"[guardian] #{p.ticket} STILL OPEN after send (rc={rc}, "
                     f"comment={getattr(res, 'comment', '')})")
        return False

    # ------------------------------------------------------------------ misc
    def _write(self, rec: dict) -> None:
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "magic": self.magic, **rec}
        try:
            self.journal.parent.mkdir(parents=True, exist_ok=True)
            with self.journal.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except Exception:                                          # noqa: BLE001
            logger.exception("guardian journal write failed")

    def state(self) -> dict:
        return {
            "armed": True,
            "hard_stop_usd": -self.hard_stop,
            "ea_stop_usd": -SEMI_MARTI_BASKET_SL_USD,
            "floating": self.last_floating,
            "legs": self.last_legs,
            "breaches": self.breaches,
            "confirm_polls": self.confirm_polls,
            "fired_count": self.fired_count,
            "last_fired": (time.strftime("%Y-%m-%dT%H:%M:%S%z",
                                         time.localtime(self.last_fired))
                           if self.last_fired else None),
        }
