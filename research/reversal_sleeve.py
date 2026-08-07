"""RSI-2 SHORT-TERM REVERSAL sleeve on US100 (validated 2026-07-15, FINDING 7).
Signal from MT5's OWN US100 D1 bars (current, not Dukascopy-lagged): LONG when RSI(2)<10 AND
close>SMA200, exit when close>SMA5. Long-only, few-day holds. magic 920633.

SHADOW by default (DRY_RUN=True) — logs the target/action WITHOUT sending. Flip DRY_RUN=False (and
add to the daily .bat's live set) only after a few shadow days look right. Disaster-stop only (mean-
reversion is stopless by design; the stop is a catastrophe backstop). Idempotent, governor-aware.

Run daily:  python research/reversal_sleeve.py
"""
import json, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd
import MetaTrader5 as mt5

SYMBOL, MAGIC, LOT = "US100", 920633, 0.01
DRY_RUN = True                                  # SHADOW 2026-08-06: dimatikan user saat rebuild VPS (menuju 'eterna').
                                                # Was LIVE (redeployed 2026-07-15 as 3rd sleeve; sized for ~$1000)
ENTRY_RSI, SMA_TREND, SMA_EXIT = 10, 200, 5
DISASTER_STOP_PCT = 0.05                         # catastrophe backstop only (~-$148 at 0.01; wide on purpose)
LOG = Path(r"C:\Quant\_MONITOR\reversal.jsonl")
GOV = Path(r"C:\Quant\_MONITOR\governor.json")


def _paused():
    try: return bool(json.loads(GOV.read_text(encoding="utf-8")).get("paused", False))
    except Exception: return False


def rsi(c, n=2):
    d = c.diff(); up = d.clip(lower=0).rolling(n).mean(); dn = (-d.clip(upper=0)).rolling(n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def target_dir():
    rates = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_D1, 0, 400)
    c = pd.Series([r["close"] for r in rates])
    r2 = rsi(c, 2).values; up = (c > c.rolling(SMA_TREND).mean()).values; ex = (c > c.rolling(SMA_EXIT).mean()).values
    inpos = False
    for i in range(1, len(c)):                  # replay the state machine to today
        if not inpos and r2[i] < ENTRY_RSI and up[i]: inpos = True
        elif inpos and ex[i]: inpos = False
    return ("LONG" if inpos else "FLAT",
            {"rsi2": round(float(r2[-1]), 1), "close": round(float(c.iloc[-1]), 1),
             "sma200": round(float(c.rolling(SMA_TREND).mean().iloc[-1]), 1),
             "sma5": round(float(c.rolling(SMA_EXIT).mean().iloc[-1]), 1)})


def main():
    if not mt5.initialize(): print("MT5 init failed", mt5.last_error()); return
    mt5.symbol_select(SYMBOL, True)
    tdir, info = target_dir()
    cur = [p for p in (mt5.positions_get(symbol=SYMBOL) or []) if p.magic == MAGIC]
    cur_dir = "FLAT" if not cur else "LONG"
    paused = _paused()
    action = ("HOLD-FLAT" if cur_dir == "FLAT" else "CLOSE") if tdir == "FLAT" else \
             ("OPEN LONG" if cur_dir == "FLAT" else "HOLD")
    mode = "LIVE" if not DRY_RUN else "SHADOW"
    print(f"REVERSAL sleeve US100  {dt.datetime.utcnow():%Y-%m-%d %H:%M} UTC  [{mode}]  paused={paused}")
    print(f"  RSI2={info['rsi2']}  close={info['close']}  SMA200={info['sma200']}  SMA5={info['sma5']}")
    print(f"  target {tdir} | cur {cur_dir} | action: {action}")

    executed = "shadow-only"
    if not DRY_RUN:
        si = mt5.symbol_info(SYMBOL); tk = mt5.symbol_info_tick(SYMBOL)
        fm = si.filling_mode; fill = mt5.ORDER_FILLING_IOC if fm & 2 else (mt5.ORDER_FILLING_FOK if fm & 1 else mt5.ORDER_FILLING_RETURN)
        if action == "OPEN LONG" and not paused:
            sl = round(tk.ask * (1 - DISASTER_STOP_PCT), si.digits)
            req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": LOT, "type": mt5.ORDER_TYPE_BUY,
                   "price": tk.ask, "sl": sl, "tp": 0.0, "magic": MAGIC, "type_filling": fill, "comment": "rsi2_rev"}
            r = mt5.order_send(req); executed = "opened" if r and r.retcode == mt5.TRADE_RETCODE_DONE else f"FAIL {getattr(r,'retcode',None)}"
        elif action == "CLOSE":
            p = cur[0]
            req = {"action": mt5.TRADE_ACTION_DEAL, "symbol": SYMBOL, "volume": p.volume, "type": mt5.ORDER_TYPE_SELL,
                   "position": p.ticket, "price": tk.bid, "type_filling": fill, "magic": MAGIC, "comment": "rsi2_exit"}
            r = mt5.order_send(req); executed = "closed" if r and r.retcode == mt5.TRADE_RETCODE_DONE else f"FAIL {getattr(r,'retcode',None)}"
        else:
            executed = "hold"
    with open(LOG, "a") as f:
        f.write(json.dumps({"ts": dt.datetime.utcnow().isoformat() + "Z", "mode": mode, "target": tdir,
                            "cur": cur_dir, "action": action, "executed": executed, **info}) + "\n")
    print(f"  -> {executed}   logged {LOG}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
