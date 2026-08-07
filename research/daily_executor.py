"""GO-LIVE daily executor for the 3-sleeve book. Sleeves in LIVE_SLEEVES actually SEND orders;
the rest stay SHADOW (log-only).

2026-07-15 DEPLOY: JPY goes LIVE (magic 920632, USDJPY 0.01 LONG carry, protective disaster-SL);
GOLD & NAS stay SHADOW (the live intraday book already trades gold/NAS, and min-lot 0.01 over-
leverages them 31x/23x on this small account -> would risk an account blow-up). JPY 0.01 = $1k
notional (~3x equity); ruin needs USDJPY -30% -> safe, and it's genuinely uncorrelated + new.

Normal exit for JPY = the daily net-carry/SMA100 gate (this executor re-run flips it to FLAT and
CLOSES when the gate turns off). The hard SL is only a downtime backstop (~$38 cap) in case this
executor stops running -- it sits far below the SMA gate so it never pre-empts normal operation.

Idempotent (won't double-open); respects the governor pause flag for NEW entries.
Run daily:  python research/daily_executor.py
"""
import json, datetime as dt
from pathlib import Path
import MetaTrader5 as mt5

SIG = Path(r"C:\Quant\_MONITOR\daily_sleeve.json")
LOG = Path(r"C:\Quant\_MONITOR\daily_executor.jsonl")
GOV = Path(r"C:\Quant\_MONITOR\governor.json")
LIVE_SLEEVES = set()                         # reverted 2026-07-15 per user — nothing sends orders (all shadow)
LOT = 0.01
JPY_DISASTER_SL = 156.00                     # downtime backstop only (~$38 cap); normal exit = SMA gate

SLEEVE = {"GOLD": {"symbol": "XAUUSD", "magic": 920630},
          "NAS":  {"symbol": "US100",  "magic": 920631},
          "JPY":  {"symbol": "USDJPY", "magic": 920632}}
SWAP_YR = {"XAUUSD": (-6.30, +2.24), "US100": (-1.44, -0.31), "USDJPY": (+0.91, -5.56)}


def _paused():
    try:
        return bool(json.loads(GOV.read_text(encoding="utf-8")).get("paused", False))
    except Exception:
        return False


def _filling(si):
    fm = si.filling_mode
    if fm & 2: return mt5.ORDER_FILLING_IOC
    if fm & 1: return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def _open(sym, is_buy, magic, sl, si):
    tick = mt5.symbol_info_tick(sym); price = tick.ask if is_buy else tick.bid
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": sym, "volume": LOT,
           "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL, "price": price,
           "sl": round(sl, si.digits) if sl else 0.0, "tp": 0.0, "magic": magic,
           "type_filling": _filling(si), "type_time": mt5.ORDER_TIME_GTC, "comment": f"daily_{magic}"}
    r = mt5.order_send(req)
    ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
    print(f"      -> SEND {'BUY' if is_buy else 'SELL'} {sym} 0.01 sl={req['sl']} : "
          f"retcode={getattr(r,'retcode',None)} {'OK' if ok else 'FAIL '+str(getattr(r,'comment',''))}")
    return ok


def _close(pos, si):
    tick = mt5.symbol_info_tick(pos.symbol); is_buy = pos.type == mt5.POSITION_TYPE_BUY
    req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol, "volume": pos.volume,
           "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY, "position": pos.ticket,
           "price": tick.bid if is_buy else tick.ask, "type_filling": _filling(si),
           "magic": pos.magic, "comment": "daily_exit"}
    r = mt5.order_send(req)
    ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
    print(f"      -> CLOSE {pos.symbol} #{pos.ticket} : retcode={getattr(r,'retcode',None)} {'OK' if ok else 'FAIL'}")
    return ok


def main():
    if not mt5.initialize():
        print("MT5 init failed", mt5.last_error()); return
    sig = json.loads(SIG.read_text())
    equity = mt5.account_info().equity
    paused = _paused()
    print(f"DAILY EXECUTOR  {dt.datetime.utcnow():%Y-%m-%d %H:%M} UTC  equity=${equity:.2f}  "
          f"LIVE={sorted(LIVE_SLEEVES)}  paused={paused}\n  sig ts={sig['ts']}\n")

    decisions = []
    for name, meta in SLEEVE.items():
        s = sig["signals"][name]; sym = meta["symbol"]; magic = meta["magic"]
        tdir = s["dir"]; live = name in LIVE_SLEEVES
        mt5.symbol_select(sym, True); si = mt5.symbol_info(sym)
        cur = [p for p in (mt5.positions_get(symbol=sym) or []) if p.magic == magic]
        cur_dir = "FLAT" if not cur else ("LONG" if cur[0].type == 0 else "SHORT")

        if tdir == "FLAT":
            action = "HOLD-FLAT" if cur_dir == "FLAT" else "CLOSE"
        elif cur_dir == "FLAT":
            action = f"OPEN {tdir}"
        elif cur_dir == tdir:
            action = "HOLD"
        else:
            action = f"FLIP {cur_dir}->{tdir}"

        swap = SWAP_YR[sym][0 if tdir == "LONG" else 1] if tdir != "FLAT" else 0.0
        tag = "LIVE" if live else "shadow"
        print(f"  {name:4} {sym:7} target {tdir:5} | cur {cur_dir:5} | {action:14} | {tag:6} | carry {swap:+.2f}%/yr")

        executed = "shadow-only"
        if live:
            if action.startswith("OPEN") and not paused:
                sl = JPY_DISASTER_SL if name == "JPY" else 0.0
                executed = "opened" if _open(sym, tdir == "LONG", magic, sl, si) else "open-FAILED"
            elif action.startswith("OPEN") and paused:
                executed = "skipped (governor paused)"
            elif action == "CLOSE" or action.startswith("FLIP"):
                ok = all(_close(p, si) for p in cur)
                if action.startswith("FLIP") and ok and not paused:
                    sl = JPY_DISASTER_SL if name == "JPY" else 0.0
                    ok = _open(sym, tdir == "LONG", magic, sl, si)
                executed = "reconciled" if ok else "reconcile-FAILED"
            else:
                executed = "hold (no action)"

        decisions.append({"sleeve": name, "symbol": sym, "magic": magic, "target": tdir,
                          "cur": cur_dir, "action": action, "live": live, "executed": executed})

    snap = {"ts": dt.datetime.utcnow().isoformat() + "Z", "equity": equity, "paused": paused,
            "live_sleeves": sorted(LIVE_SLEEVES), "sig_ts": sig["ts"], "decisions": decisions}
    with open(LOG, "a") as f:
        f.write(json.dumps(snap) + "\n")
    print(f"\nlogged -> {LOG}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
