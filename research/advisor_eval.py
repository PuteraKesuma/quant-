"""Evaluate the shadow advisor on REAL closed trades. Two honest questions:

  (A) Does a 'CAUTION' read predict worse trades than a 'CONFIRM' read?
  (B) Would TP-ing at Claude's suggested level have BEATEN the brain's channel exit, on the
      SAME trades? (the user's TP idea, measured head-to-head with zero live risk)

It joins each verdict in advisor_journal.jsonl to that position's realized PnL (by ticket,
via MT5 closed-deal history). For (B) it reconstructs the M1 path between entry and exit to
check whether suggested_tp would have been hit first, and prices the what-if using the
trade's own realized $/point. The advisor earns the right to ever ACT only if the data here
clearly favours it — on a meaningful sample. Run:  python research/advisor_eval.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, r"C:\Quant")
JOURNAL = Path(r"C:\Quant\advisor_journal.jsonl")


def _deals(mt5, ticket):
    d = mt5.history_deals_get(position=ticket)
    return list(d) if d else []


def realized(mt5, deals):
    """(total_pnl, entry_price, exit_price, open_dt, exit_dt, broker_symbol) or None if open."""
    ins = [d for d in deals if d.entry == mt5.DEAL_ENTRY_IN]
    outs = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
    if not ins or not outs:
        return None
    pnl = sum(d.profit + d.swap + d.commission for d in deals)
    out = outs[-1]
    return (pnl, ins[0].price, out.price,
            datetime.fromtimestamp(ins[0].time, timezone.utc),
            datetime.fromtimestamp(out.time, timezone.utc), out.symbol)


def tp_hit(mt5, symbol, tf_from, tf_to, direction, tp):
    """Was tp touched on the M1 path between entry and exit?"""
    rates = mt5.copy_rates_range(symbol, mt5.TIMEFRAME_M1, tf_from, tf_to)
    if rates is None or len(rates) == 0:
        return False
    if direction == "LONG":
        return any(r["high"] >= tp for r in rates)
    return any(r["low"] <= tp for r in rates)


def main() -> None:
    if not JOURNAL.exists():
        print("no advisor_journal.jsonl yet -- advisor hasn't logged any entries."); return
    rows = [json.loads(l) for l in JOURNAL.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("ticket")]
    print(f"advisor journal: {len(rows)} annotated entries\n")

    import MetaTrader5 as mt5
    if not mt5.initialize():
        print("MT5 initialize failed:", mt5.last_error()); return
    mt5.history_select(datetime(2026, 1, 1, tzinfo=timezone.utc), datetime.now(timezone.utc))

    buckets = {"CONFIRM": [], "NEUTRAL": [], "CAUTION": []}
    open_n = 0
    sum_actual = sum_tp = 0.0
    tp_rows = 0; tp_would_hit = 0
    for r in rows:
        rz = realized(mt5, _deals(mt5, int(r["ticket"])))
        if rz is None:
            open_n += 1; continue
        pnl, e_px, x_px, o_dt, x_dt, sym = rz
        if r.get("verdict") in buckets:
            buckets[r["verdict"]].append(pnl)
        # (B) what-if Claude-TP vs channel exit
        tp = r.get("suggested_tp"); d = r.get("direction")
        if tp and d in ("LONG", "SHORT"):
            dirn = 1 if d == "LONG" else -1
            delta = (x_px - e_px) * dirn
            good_side = (tp > e_px) if d == "LONG" else (tp < e_px)
            if abs(delta) > 1e-9 and good_side:
                tp_rows += 1
                dpp = pnl / delta                       # realized $ per favourable price unit
                if tp_hit(mt5, sym, o_dt, x_dt, d, tp):
                    tp_would_hit += 1
                    whatif = (tp - e_px) * dirn * dpp    # banked at Claude's TP
                else:
                    whatif = pnl                         # never reached -> rode to channel exit
                sum_actual += pnl; sum_tp += whatif
    mt5.shutdown()

    print("(A) verdict vs realized PnL:")
    print(f"  {'verdict':8} {'n':>4} {'win%':>6} {'avg $':>9} {'total $':>10}")
    for v in ("CONFIRM", "NEUTRAL", "CAUTION"):
        xs = buckets[v]
        if not xs:
            print(f"  {v:8} {0:>4}      -         -          -"); continue
        win = 100 * sum(1 for x in xs if x > 0) / len(xs)
        print(f"  {v:8} {len(xs):>4} {win:>5.0f}% {sum(xs)/len(xs):>+8.2f} {sum(xs):>+9.2f}")
    print(f"  ({open_n} still-open entries skipped)")
    c, k = buckets["CONFIRM"], buckets["CAUTION"]
    if len(c) >= 10 and len(k) >= 10:
        ac, ak = sum(c)/len(c), sum(k)/len(k)
        print(f"  -> CONFIRM avg ${ac:+.2f} vs CAUTION avg ${ak:+.2f}: "
              + ("CAUTION clearly worse, EARNS a filter" if ak < ac - 0.5
                 else "no reliable edge yet, keep shadow-only"))
    else:
        print("  -> sample too small (need >=10 each) -- keep collecting.")

    print("\n(B) Claude-TP vs brain channel-exit, same trades:")
    if tp_rows == 0:
        print("  no closed trades with a valid suggested_tp yet.")
    else:
        print(f"  {tp_rows} trades, Claude's TP would have been hit on {tp_would_hit}.")
        print(f"  channel-exit total ${sum_actual:+.2f}  vs  Claude-TP total ${sum_tp:+.2f}")
        better = "Claude-TP BEATS channel exit" if sum_tp > sum_actual + 0.5 else \
                 ("roughly tied" if abs(sum_tp - sum_actual) <= 0.5 else
                  "channel exit WINS (TP caps winners -- as backtest predicted)")
        print(f"  -> {better}")
        if tp_rows < 10:
            print("  (sample small -- read as directional, not conclusive)")


if __name__ == "__main__":
    main()
