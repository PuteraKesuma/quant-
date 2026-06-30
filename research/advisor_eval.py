"""Evaluate the shadow advisor: does a 'CAUTION' read actually predict worse trades?

Joins each verdict in advisor_journal.jsonl to the realized PnL of that position (by
ticket, via MT5 closed-deal history) and reports, per verdict bucket, the count / win
rate / average PnL / total. The advisor earns the right to ever FILTER trades only if
CAUTION trades are reliably worse than CONFIRM trades on a meaningful sample.

This is the forward-test that makes the (un-backtestable) LLM signal honest. Run after a
few weeks of live entries:  python research/advisor_eval.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, r"C:\Quant")
JOURNAL = Path(r"C:\Quant\advisor_journal.jsonl")


def realized_pnl(mt5, ticket: int):
    """Total realized PnL (profit+swap+commission) for a closed position; None if still open."""
    deals = mt5.history_deals_get(position=ticket)
    if not deals:
        return None
    closed = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
    if not closed:
        return None                              # entry present, no exit yet -> still open
    return sum(d.profit + d.swap + d.commission for d in deals)


def main() -> None:
    if not JOURNAL.exists():
        print("no advisor_journal.jsonl yet -- advisor hasn't logged any entries.")
        return
    rows = [json.loads(l) for l in JOURNAL.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"advisor journal: {len(rows)} annotated entries\n")

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5 initialize failed:", mt5.last_error()); return
    # make sure deal history is loaded
    mt5.history_select(datetime(2026, 1, 1, tzinfo=timezone.utc),
                       datetime.now(timezone.utc))

    buckets: dict[str, list[float]] = {"CONFIRM": [], "NEUTRAL": [], "CAUTION": []}
    open_n = 0
    for r in rows:
        v = r.get("verdict")
        if v not in buckets:
            continue
        pnl = realized_pnl(mt5, int(r["ticket"]))
        if pnl is None:
            open_n += 1
            continue
        buckets[v].append(pnl)
    mt5.shutdown()

    print(f"  {'verdict':8} {'n':>4} {'win%':>6} {'avg $':>9} {'total $':>10}")
    for v in ("CONFIRM", "NEUTRAL", "CAUTION"):
        xs = buckets[v]
        if not xs:
            print(f"  {v:8} {0:>4}      -         -          -")
            continue
        win = 100 * sum(1 for x in xs if x > 0) / len(xs)
        avg = sum(xs) / len(xs)
        print(f"  {v:8} {len(xs):>4} {win:>5.0f}% {avg:>+8.2f} {sum(xs):>+9.2f}")
    print(f"\n  ({open_n} still-open entries skipped)")
    c, k = buckets["CONFIRM"], buckets["CAUTION"]
    if len(c) >= 10 and len(k) >= 10:
        ac, ak = sum(c) / len(c), sum(k) / len(k)
        verdict = ("EARNS a filter: CAUTION clearly worse" if ak < ac - 0.5
                   else "NO edge yet: CAUTION not reliably worse -- keep shadow-only")
        print(f"\n  CONFIRM avg ${ac:+.2f} vs CAUTION avg ${ak:+.2f} -> {verdict}")
    else:
        print("\n  sample too small (need >=10 CONFIRM and >=10 CAUTION) -- keep collecting.")


if __name__ == "__main__":
    main()
