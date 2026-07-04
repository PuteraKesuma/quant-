# VPS recovery / redeploy guide (updated 2026-07-04)

This branch (`vps-zrev-live`) is the full live trading system. If the VPS is replaced,
everything here is recoverable from GitHub EXCEPT the few VPS-local secrets/data below.
A full ZIP backup (code + data + .env + journals + this repo as `vps-zrev-live.bundle`)
is kept OFF-VPS by the user — that zip contains secrets, never upload/share it.

## What IS in this repo (recoverable)
- `config.yaml` — live strategy slots (the deployed brain config).
- `pipeline/` — FastAPI signal brain (`live/server.py`), strategies (`live/signal.py`),
  the two standalone order managers (`live/liquidity_manager.py` pending LIMITs,
  `live/orb_stop_manager.py` pending STOPs), shadow advisor (`live/advisor.py`),
  vision capture (`vision/tv_capture.py`), backtests (`backtest/`).
- `research/` — all audits/validation (reproducible), incl. `book_validation_500.py`,
  `portfolio_best.py`, `signal_logic_audit.py`, `edge_refinements.py`.
- `_DOC/` — audit reports, forward-test protocol, validation summaries.
- `requirements.txt`, `mt5_ea/SignalExecutor.mq5`, all `START_*.bat` launchers,
  `_MONITOR/watchdog_brain.ps1`.

## What is NOT in git (in the ZIP backup / re-create on a new VPS)
- **`.env`** — `ANTHROPIC_API_KEY=...` (advisor). Secret, never committed.
- `data/Level_0_Raw/*.duckdb` — historical Dukascopy 1m data (research only; live
  trading does NOT need it — the brain pulls live bars from MT5). Re-fetchable via
  the pipeline if lost.
- MetaTrader 5 terminal + FBS-Demo login ("Save account information" ON for
  auto-login) + `SignalExecutor` EA attached to XAUUSD and US100 charts, Algo Trading ON.
- `_MONITOR/jurnal.md`, `advisor_journal.jsonl`, `_MONITOR/forward_test.json`
  (forward-test start marker) — operational journals (in the ZIP).

## Redeploy steps on a fresh VPS (Windows)
1. Install Python 3.11; `git clone` this repo, branch `vps-zrev-live` (or
   `git clone vps-zrev-live.bundle` from the ZIP backup if GitHub is unavailable).
2. `pip install -r requirements.txt`
3. `python -m playwright install chromium` AND the MS VC++ 2015-2022 x64
   redistributable (https://aka.ms/vs/17/release/vc_redist.x64.exe) — needed for the
   advisor's TradingView capture (greenlet DLL).
4. Restore `.env` (from the ZIP) — or create it with `ANTHROPIC_API_KEY=...`.
5. Install MT5, log into the FBS-Demo account, attach `mt5_ea/SignalExecutor.mq5` to
   the XAUUSD and US100 charts, enable "Algo Trading".
6. Start everything once by hand to verify: `START_TRADING.bat` (preflight + brain),
   `START_ADVISOR.bat`, `START_LIQMGR.bat`, `START_ORBMGR.bat`, and
   `powershell -ExecutionPolicy Bypass -File _MONITOR\watchdog_brain.ps1`.
7. Make it survive reboots: Startup-folder shortcuts for **MetaTrader 5, ORB Trading
   Brain, ORB Brain Watchdog, Shadow Advisor, Liquidity Manager, ORB Stop Manager**
   + Windows auto-logon (Sysinternals Autologon). Close RDP with **Disconnect (X),
   never Sign out**. The watchdog auto-relaunches brain/advisor/liqmgr/orbmgr and MT5.
8. Restore `_MONITOR/forward_test.json` from the ZIP if continuing the forward test
   (otherwise the tracker restamps a new start).

## Live config summary (deployed 2026-07-04 — the FULL book)
- `zrev_xau` (magic 920622, brain+EA): always-in Donchian S&R XAU 1H, entry20/exit20,
  H1 EMA100 + Daily SMA50 dual trend gate, z-score dynamic lot with conservative caps
  (lot_max 0.02, lot_per_balance 0.0000067), atr_stop_mult 3.0.
- `orb30_nas` (magic 920617, **orb_stop_manager**, EA slot disabled): NAS NY 30m ORB
  1:1, DST open + Daily SMA50 gate + 0.5R breakeven + 20:00 UTC close — entries via a
  REAL pending STOP at the range boundary (fills AT the level, no M1-close slippage).
- `liquidity_limit` (magic 920625, **liquidity_manager**, EA slot disabled): 15min
  Supertrend(21, 5.5) flat-band limit, BUY at support / SELL at resistance, $13/$26,
  one position, REAL pending LIMIT re-priced continuously.
- **Shadow advisor** (Sonnet): annotates entries on 920617/920622/920625 with
  CONFIRM/CAUTION — insight only, never blocks/places orders.
- `vision_smc_xau` (920621): RETIRED. `mr_xau` (920623): artifact, never enable.
- EA is strategy-agnostic and NEVER modified (`BreakevenMagics=920621` keeps the EA's
  own breakeven away from the managers' magics).
